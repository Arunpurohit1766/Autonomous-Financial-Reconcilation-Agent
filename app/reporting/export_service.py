"""Comprehensive export service for consolidated reconciliation and compliance packages."""

import csv
import io
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session

from app.persistence.models.all_models import (
    ReconciliationJob,
    ExceptionRecord,
    MatchRecord,
    ReportRecord,
)
from app.persistence.models.audit import AuditLog


def generate_reconciliation_csv_export(db: Session, job_id: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Generates a structured multi-section CSV string capturing:
    1. Summary metadata
    2. Matched pairs with matched rules
    3. Exceptions with human-in-the-loop decisions
    """
    job = db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id).first()
    if not job:
        return None, f"Reconciliation job '{job_id}' not found."

    output = io.StringIO()
    writer = csv.writer(output)

    # Section 1: Executive Header
    writer.writerow(["=== RECONCILIATION SUMMARY ==="])
    writer.writerow(["Job ID", job.id])
    writer.writerow(["Tenant ID", job.tenant_id])
    writer.writerow(["Status", job.status.value if hasattr(job.status, "value") else str(job.status)])
    writer.writerow(["Total Invoices", job.total_invoices])
    writer.writerow(["Total Payments", job.total_payments])
    writer.writerow(["Matched Count", job.matched_count])
    writer.writerow(["Exception Count", job.exception_count])
    writer.writerow(["Export Generated At (UTC)", datetime.now(timezone.utc).isoformat()])
    writer.writerow([])

    # Section 2: Matched Pairs
    writer.writerow(["=== MATCHED RECORDS ==="])
    writer.writerow(["Match ID", "Invoice ID", "Payment ID", "Rule ID", "Matched At"])
    matches = db.query(MatchRecord).filter(MatchRecord.job_id == job_id).all()
    for m in matches:
        writer.writerow([
            m.id,
            m.invoice_id,
            m.payment_id,
            getattr(m, "rule_id", "EXACT_MATCH"),
            m.created_at.isoformat() if getattr(m, "created_at", None) else "",
        ])
    writer.writerow([])

    # Section 3: Exceptions & Review Status
    writer.writerow(["=== EXCEPTIONS & HUMAN RESOLUTIONS ==="])
    writer.writerow(["Exception ID", "Type", "Status", "Entity ID", "Entity Type", "Amount", "Reason"])
    exceptions = db.query(ExceptionRecord).filter(ExceptionRecord.job_id == job_id).all()
    for exc in exceptions:
        writer.writerow([
            exc.id,
            exc.type.value if hasattr(exc.type, "value") else str(exc.type),
            exc.status.value if hasattr(exc.status, "value") else str(exc.status),
            exc.entity_id or "",
            exc.entity_type or "",
            float(exc.amount) if exc.amount is not None else 0.0,
            exc.reason or "",
        ])

    return output.getvalue(), None


def generate_full_compliance_audit_json(db: Session, job_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Builds a complete compliance JSON package with full audit trail histories
    for SOX/financial regulatory verification.
    """
    job = db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id).first()
    if not job:
        return None, f"Reconciliation job '{job_id}' not found."

    # Fetch report payload if generated
    report_row = db.query(ReportRecord).filter(ReportRecord.job_id == job_id).first()
    report_payload = report_row.payload if report_row else None
    if isinstance(report_payload, str):
        try:
            report_payload = json.loads(report_payload)
        except Exception:
            pass

    # Fetch all exceptions for this job
    exceptions = db.query(ExceptionRecord).filter(ExceptionRecord.job_id == job_id).all()
    exception_ids = [e.id for e in exceptions]

    # Fetch all audit logs for these exceptions and this job
    audit_logs = (
        db.query(AuditLog)
        .filter(AuditLog.target_id.in_([job_id] + exception_ids))
        .order_by(AuditLog.timestamp.asc())
        .all()
    )

    audit_trail_data = [
        {
            "id": a.id,
            "target_type": a.target_type,
            "target_id": a.target_id,
            "action": a.action,
            "actor": a.actor,
            "old_status": a.old_status,
            "new_status": a.new_status,
            "details": a.details or {},
            "timestamp": a.timestamp.isoformat() if a.timestamp else None,
        }
        for a in audit_logs
    ]

    compliance_package = {
        "export_metadata": {
            "format_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "compliance_standard": "SOX-404-FINANCIAL-AUDIT",
        },
        "job": {
            "id": job.id,
            "tenant_id": job.tenant_id,
            "status": job.status.value if hasattr(job.status, "value") else str(job.status),
            "total_invoices": job.total_invoices,
            "total_payments": job.total_payments,
            "matched_count": job.matched_count,
            "exception_count": job.exception_count,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if getattr(job, "finished_at", None) else None,
        },
        "engine_report": report_payload,
        "exceptions_count": len(exceptions),
        "audit_ledger_trail": audit_trail_data,
    }

    return compliance_package, None
