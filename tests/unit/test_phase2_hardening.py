"""Unit tests verifying atomic job claims, operational recovery, and report consistency."""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import io
from fastapi.testclient import TestClient

from app.main import app
from app.persistence.database import SessionLocal
from app.persistence.models import ReconciliationJob, Tenant, ExceptionRecord, ReportRecord, SourceFile
from app.workflows.recovery import recover_stuck_jobs
from app.workflows.runner import execute_reconciliation_job, claim_job_atomically
from app.integrations.storage.local import storage_adapter

client = TestClient(app)


def test_atomic_job_claim_prevents_race_condition():
    """Verify atomic conditional UPDATE allows only 1 worker to claim a QUEUED job."""
    with SessionLocal() as db:
        if not db.query(Tenant).filter(Tenant.id == "tenant_race_test").first():
            db.add(Tenant(id="tenant_race_test", name="Race Test Tenant"))
            db.commit()

        job = ReconciliationJob(tenant_id="tenant_race_test", status="QUEUED")
        db.add(job)
        db.commit()
        job_id = job.id

        # Worker 1 attempts claim -> Must succeed
        assert claim_job_atomically(job_id, db) is True

        # Worker 2 attempts claim concurrently on same job -> Must fail
        assert claim_job_atomically(job_id, db) is False


def test_operational_recovery_via_admin_endpoint():
    """Verify recovery is executable via administrative operations endpoint."""
    with SessionLocal() as db:
        if not db.query(Tenant).filter(Tenant.id == "tenant_alpha").first():
            db.add(Tenant(id="tenant_alpha", name="Tenant Alpha"))
            db.commit()

        stuck_time = datetime.now(timezone.utc) - timedelta(minutes=45)
        job = ReconciliationJob(
            tenant_id="tenant_alpha",
            status="PROCESSING",
            started_at=stuck_time,
        )
        db.add(job)
        db.commit()
        stuck_id = job.id

    # Call admin recovery endpoint
    headers = {"Authorization": "Bearer key_admin_alpha"}
    res = client.post("/api/v1/jobs/recover", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert stuck_id in data["recovered_job_ids"]

    # Verify status changed to FAILED
    with SessionLocal() as db:
        refreshed = db.query(ReconciliationJob).filter(ReconciliationJob.id == stuck_id).first()
        assert refreshed.status == "FAILED"


def test_dirty_csv_errors_included_in_audit_report():
    """Verify that CSV row validation errors appear in the audit JSON report items and summary count."""
    dirty_invoices = """invoice_id,customer_reference,issued_at,currency,subtotal,tax_amount,discount_amount,total_amount,payment_reference
INV-VALID-1,CUST-A,2024-10-01T10:00:00Z,USD,100.00,0.00,0.00,100.00,
INV-INVALID-2,CUST-B,BAD_DATE,USD,100.00,0.00,0.00,100.00,
"""
    clean_payments = """payment_id,invoice_reference,customer_reference,paid_at,currency,gross_amount,fee_amount,net_amount,provider_status
PAY-1,INV-VALID-1,CUST-A,2024-10-01T11:00:00Z,USD,100.00,0.00,100.00,succeeded
"""
    with SessionLocal() as db:
        if not db.query(Tenant).filter(Tenant.id == "tenant_report_check").first():
            db.add(Tenant(id="tenant_report_check", name="Report Check Tenant"))
            db.commit()

        job = ReconciliationJob(tenant_id="tenant_report_check", status="QUEUED")
        db.add(job)
        db.flush()

        inv_meta = storage_adapter.save_file(dirty_invoices.encode(), "dirty_invoices.csv")
        pmt_meta = storage_adapter.save_file(clean_payments.encode(), "clean_payments.csv")

        f_inv = SourceFile(
            tenant_id="tenant_report_check", job_id=job.id, file_kind="invoice",
            original_filename="dirty_invoices.csv", storage_key=inv_meta["storage_key"],
            sha256=inv_meta["sha256"], size_bytes=inv_meta["size_bytes"],
        )
        f_pmt = SourceFile(
            tenant_id="tenant_report_check", job_id=job.id, file_kind="payment",
            original_filename="clean_payments.csv", storage_key=pmt_meta["storage_key"],
            sha256=pmt_meta["sha256"], size_bytes=pmt_meta["size_bytes"],
        )
        db.add(f_inv)
        db.add(f_pmt)
        db.flush()

        job.invoice_file_id = f_inv.id
        job.payment_file_id = f_pmt.id
        db.commit()

        execute_reconciliation_job(job.id, db)

        # Verify job exception_count matches the report's total_exceptions
        refreshed = db.query(ReconciliationJob).filter(ReconciliationJob.id == job.id).first()
        report_record = db.query(ReportRecord).filter(ReportRecord.job_id == job.id).first()

        assert refreshed.status == "COMPLETED_WITH_EXCEPTIONS"
        payload = report_record.report_payload
        assert payload["summary"]["total_exceptions"] == refreshed.exception_count
        assert any(
            item.get("rule_id") == "RULE-009-ROW-VALIDATION-ERROR"
            for item in payload["items"]
        )
