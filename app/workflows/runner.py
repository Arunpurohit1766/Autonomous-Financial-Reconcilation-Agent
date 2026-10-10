"""Asynchronous reconciliation job execution workflow with strict lease fencing."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
import json
import logging
from typing import Optional, List
import uuid

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domain.enums import JobStatus, ExceptionType, ExceptionStatus
from app.domain.transitions import validate_job_transition
from app.persistence.database import SessionLocal
from app.persistence.models import (
    ReconciliationJob, SourceFile, InvoiceRecord, PaymentRecord,
    MatchRecord, ExceptionRecord, ReportRecord
)
from app.integrations.storage.local import storage_adapter
from app.ingestion.csv_parser import parse_invoices_csv, parse_payments_csv
from app.reconciliation.matcher import DeterministicMatcher
from app.reconciliation.calculator import ReconciliationCalculator, ReconciliationItem
from app.reporting.json_report import generate_json_report

logger = logging.getLogger(__name__)

DEFAULT_LEASE_SECONDS = 60


class LostJobOwnershipError(Exception):
    """Raised when a worker has lost its lease/fencing token before writing."""
    pass


def claim_job(db: Session, job_id: str, lease_duration_seconds: int = DEFAULT_LEASE_SECONDS) -> Optional[str]:
    now = datetime.utcnow()
    worker_token = str(uuid.uuid4())
    lease_expires = now + timedelta(seconds=lease_duration_seconds)

    stmt = (
        update(ReconciliationJob)
        .where(
            ReconciliationJob.id == job_id,
            ReconciliationJob.status == JobStatus.QUEUED.value
        )
        .values(
            status=JobStatus.PROCESSING.value,
            started_at=now,
            lease_expires_at=lease_expires,
            lease_token=worker_token,
        )
    )
    result = db.execute(stmt)
    db.commit()

    if result.rowcount == 1:
        return worker_token
    return None


def claim_job_atomically(arg1, arg2) -> bool:
    if isinstance(arg1, str):
        job_id, db = arg1, arg2
    else:
        db, job_id = arg1, arg2
    token = claim_job(db, job_id)
    return token is not None


def heartbeat_lease(db: Session, job_id: str, worker_token: str, extension_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
    now = datetime.utcnow()
    new_expires = now + timedelta(seconds=extension_seconds)

    stmt = (
        update(ReconciliationJob)
        .where(
            ReconciliationJob.id == job_id,
            ReconciliationJob.lease_token == worker_token,
            ReconciliationJob.status == JobStatus.PROCESSING.value,
        )
        .values(lease_expires_at=new_expires)
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount == 1


def verify_worker_ownership(db: Session, job_id: str, worker_token: str) -> bool:
    job = (
        db.query(ReconciliationJob)
        .filter(
            ReconciliationJob.id == job_id,
            ReconciliationJob.lease_token == worker_token,
            ReconciliationJob.status == JobStatus.PROCESSING.value,
        )
        .first()
    )
    return job is not None


def _read_source_file_content(source_file: SourceFile) -> str:
    if hasattr(storage_adapter, "retrieve_file"):
        target_key = source_file.storage_key or source_file.storage_path
        data = storage_adapter.retrieve_file(target_key)
        if isinstance(data, bytes):
            return data.decode("utf-8")
        return str(data)
    elif hasattr(storage_adapter, "retrieve_file_bytes"):
        target_path = source_file.storage_path or source_file.storage_key
        return storage_adapter.retrieve_file_bytes(target_path).decode("utf-8")
    else:
        path = source_file.storage_path or source_file.storage_key
        with open(path, "r", encoding="utf-8") as f:
            return f.read()


def execute_reconciliation_job(
    job_id: str,
    db: Optional[Session] = None,
    worker_token: Optional[str] = None
) -> bool:
    owns_session = False
    if db is None:
        db = SessionLocal()
        owns_session = True

    try:
        if worker_token is None:
            job = db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id).first()
            if not job:
                return False
            if job.status == JobStatus.QUEUED.value:
                worker_token = claim_job(db, job_id)
                if worker_token is None:
                    return False
            elif job.status == JobStatus.PROCESSING.value:
                worker_token = job.lease_token

        job = db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id).first()
        if not job or (worker_token and job.lease_token != worker_token):
            return False

        tenant_id = job.tenant_id
        inv_file = db.query(SourceFile).filter(SourceFile.id == job.invoice_file_id).first()
        pay_file = db.query(SourceFile).filter(SourceFile.id == job.payment_file_id).first()

        if not inv_file or not pay_file:
            now_ts = datetime.utcnow()
            job.status = JobStatus.FAILED.value
            job.error_summary = "Missing source file records."
            job.completed_at = now_ts
            job.finished_at = now_ts
            job.lease_token = None
            db.commit()
            return False

        try:
            inv_text = _read_source_file_content(inv_file)
            pmt_text = _read_source_file_content(pay_file)

            invoices, inv_errors = parse_invoices_csv(inv_text)
            payments, pmt_errors = parse_payments_csv(pmt_text)

            if worker_token:
                heartbeat_lease(db, job_id, worker_token)

            for inv in invoices:
                cust_ref = getattr(inv, "customer_reference", getattr(inv, "customer_id", ""))
                inv_dt = getattr(inv, "issued_at", getattr(inv, "invoice_date", datetime.utcnow()))
                due_dt = getattr(inv, "due_date", inv_dt)
                inv_dt_str = inv_dt.isoformat() if hasattr(inv_dt, "isoformat") else str(inv_dt)
                due_dt_str = due_dt.isoformat() if hasattr(due_dt, "isoformat") else str(due_dt)

                db.add(InvoiceRecord(
                    tenant_id=tenant_id, job_id=job_id, invoice_id=inv.invoice_id,
                    customer_id=cust_ref, invoice_date=inv_dt_str,
                    due_date=due_dt_str, currency=inv.currency,
                    subtotal_amount=inv.subtotal, tax_amount=inv.tax_amount,
                    total_amount=inv.total_amount, status="INGESTED"
                ))

            for pmt in payments:
                pay_dt = getattr(pmt, "paid_at", getattr(pmt, "payment_date", datetime.utcnow()))
                settle_dt = getattr(pmt, "settlement_date", pay_dt)
                pay_dt_str = pay_dt.isoformat() if hasattr(pay_dt, "isoformat") else str(pay_dt)
                settle_dt_str = settle_dt.isoformat() if hasattr(settle_dt, "isoformat") else str(settle_dt)

                db.add(PaymentRecord(
                    tenant_id=tenant_id, job_id=job_id, payment_id=pmt.payment_id,
                    payment_date=pay_dt_str, settlement_date=settle_dt_str,
                    currency=pmt.currency, gross_amount=pmt.gross_amount, fee_amount=pmt.fee_amount,
                    net_amount=pmt.net_amount, provider_transaction_id=getattr(pmt, "provider_transaction_id", None),
                    reference_id=getattr(pmt, "reference_id", None), status="INGESTED"
                ))

            matcher = DeterministicMatcher()
            match_out = matcher.match(invoices, payments)
            if len(match_out) == 4:
                match_results, unmatched_invoices, unmatched_payments, duplicate_payments = match_out
            else:
                match_results, unmatched_invoices, unmatched_payments = match_out
                duplicate_payments = []

            calculator = ReconciliationCalculator()
            items: List[ReconciliationItem] = calculator.reconcile(
                match_results=match_results,
                unmatched_valid_payments=unmatched_payments,
                duplicate_payments=duplicate_payments
            )

            # Build parsing error items
            parsing_items: List[ReconciliationItem] = []
            all_errors = inv_errors + pmt_errors
            for err in all_errors:
                msg = getattr(err, "error_message", getattr(err, "message", str(err)))
                row_no = getattr(err, "row_number", "UNKNOWN")
                parsing_items.append(
                    ReconciliationItem(
                        invoice_id=None,
                        payment_id=None,
                        is_reconciled=False,
                        exception_type=ExceptionType.DATA_QUALITY_ERROR,
                        difference_amount=Decimal("0.00"),
                        currency="USD",
                        rule_id="RULE-009-ROW-VALIDATION-ERROR",
                        evidence={"error": f"CSV validation error on row {row_no}: {msg}"},
                    )
                )

            # Generate JSON report & serialize cleanly to dict
            report = generate_json_report(
                job_id=job_id,
                items=items,
                parsing_error_items=parsing_items,
            )
            if hasattr(report, "model_dump_json"):
                report_dict = json.loads(report.model_dump_json())
            elif hasattr(report, "model_dump"):
                report_dict = report.model_dump(mode="json")
            else:
                report_dict = json.loads(report.json())

            # Atomic ownership check before final write
            if worker_token and not verify_worker_ownership(db, job_id, worker_token):
                logger.error(f"Fencing check failed for job {job_id}. Worker lost lease token.")
                raise LostJobOwnershipError(f"Worker lost lease token for job {job_id}.")

            # Map match results by (invoice_id, payment_id) to preserve actual method and confidence
            match_metadata_map = {}
            for mr in match_results:
                inv_id = getattr(mr.invoice, "invoice_id", None)
                pmt_id = getattr(mr.payment, "payment_id", None)
                if inv_id and pmt_id:
                    score = getattr(mr, "candidate_score", 1.0)
                    meth = getattr(mr, "method", "EXACT")
                    if hasattr(meth, "value"):
                        meth = meth.value
                    match_metadata_map[(inv_id, pmt_id)] = {
                        "method": str(meth),
                        "confidence": Decimal(str(score)),
                    }

            for item in items:
                diff_amt = item.difference_amount if item.difference_amount is not None else Decimal("0.00")
                if item.invoice_id and item.payment_id:
                    actual_meta = match_metadata_map.get(
                        (item.invoice_id, item.payment_id),
                        {"method": getattr(item, "rule_id", "EXACT"), "confidence": Decimal("1.00") if item.is_reconciled else Decimal("0.80")}
                    )
                    db.add(MatchRecord(
                        tenant_id=tenant_id, job_id=job_id,
                        invoice_id=item.invoice_id, payment_id=item.payment_id,
                        method=actual_meta["method"],
                        status="MATCHED" if item.is_reconciled else "EXCEPTION",
                        confidence=actual_meta["confidence"],
                        variance_amount=diff_amt,
                    ))
                if not item.is_reconciled:
                    db.add(ExceptionRecord(
                        tenant_id=tenant_id, job_id=job_id,
                        type=item.exception_type.value if hasattr(item.exception_type, "value") else str(item.exception_type),
                        status="OPEN",
                        entity_id=item.invoice_id or item.payment_id or "UNKNOWN",
                        entity_type="INVOICE" if item.invoice_id else "PAYMENT",
                        reason=str(item.evidence) if isinstance(item.evidence, dict) else (item.evidence or "Discrepancy detected"),
                        amount=diff_amt,
                    ))

            for perr in parsing_items:
                db.add(ExceptionRecord(
                    tenant_id=tenant_id, job_id=job_id,
                    type=ExceptionType.DATA_QUALITY_ERROR.value, status="OPEN",
                    entity_id="ROW-ERROR", entity_type="ROW",
                    reason=str(perr.evidence) if isinstance(perr.evidence, dict) else (perr.evidence or "CSV validation error"), amount=Decimal("0.00"),
                ))

            db.add(ReportRecord(
                tenant_id=tenant_id, job_id=job_id, payload=report_dict
            ))

            has_exceptions = any(not item.is_reconciled for item in items) or len(parsing_items) > 0
            final_status = "COMPLETED_WITH_EXCEPTIONS" if has_exceptions else "COMPLETED"
            now_ts = datetime.utcnow()
            job.status = final_status
            job.completed_at = now_ts
            job.finished_at = now_ts
            job.total_invoices = len(invoices)
            job.total_payments = len(payments)
            job.matched_count = sum(1 for it in items if it.is_reconciled)
            job.exception_count = sum(1 for it in items if not it.is_reconciled) + len(parsing_items)
            job.lease_token = None
            db.commit()
            return True

        except LostJobOwnershipError:
            db.rollback()
            return False
        except Exception as exc:
            db.rollback()
            logger.exception(f"Job {job_id} encountered processing error: {exc}")
            if worker_token is None or verify_worker_ownership(db, job_id, worker_token):
                now_ts = datetime.utcnow()
                job.status = JobStatus.FAILED.value
                job.error_summary = str(exc)
                job.completed_at = now_ts
                job.finished_at = now_ts
                job.lease_token = None
                db.commit()
            return False

    finally:
        if owns_session:
            db.close()


run_reconciliation_workflow = execute_reconciliation_job


def process_all_queued_jobs(db: Optional[Session] = None):
    owns_session = False
    if db is None:
        db = SessionLocal()
        owns_session = True

    try:
        queued_jobs = (
            db.query(ReconciliationJob.id)
            .filter(ReconciliationJob.status == JobStatus.QUEUED.value)
            .all()
        )
        job_ids = [row[0] for row in queued_jobs]

        for jid in job_ids:
            execute_reconciliation_job(jid, db)
        return job_ids
    finally:
        if owns_session:
            db.close()


async def background_worker_poll_loop(poll_interval_seconds: float = 1.0, stop_event: Optional[asyncio.Event] = None):
    while stop_event is None or not stop_event.is_set():
        try:
            await asyncio.to_thread(process_all_queued_jobs)
        except Exception as e:
            logger.error(f"Error in background worker poll loop: {e}")
        try:
            await asyncio.sleep(poll_interval_seconds)
        except asyncio.CancelledError:
            break
