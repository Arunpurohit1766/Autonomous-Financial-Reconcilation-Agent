"""Service implementing HITL decision logic, state validation, and audit recording."""

from datetime import datetime, timezone
import json
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from app.domain.enums import ExceptionStatus
from app.persistence.models.all_models import ExceptionRecord
from app.persistence.models.audit import AuditLog
from app.schemas.hitl import ExceptionResolutionRequest, AuditLogRecord, ExceptionDetailResponse


ALLOWED_HITL_TRANSITIONS = {
    "APPROVE": ExceptionStatus.APPROVED,
    "RESOLVE": ExceptionStatus.RESOLVED,
    "REJECT": ExceptionStatus.REJECTED,
    "OVERRIDE": ExceptionStatus.RESOLVED,
    "INVESTIGATE": ExceptionStatus.INVESTIGATING,
}

TERMINAL_STATUSES = {
    ExceptionStatus.RESOLVED,
    ExceptionStatus.APPROVED,
    ExceptionStatus.REJECTED,
}


def resolve_exception(
    db: Session,
    exception_id: str,
    req: ExceptionResolutionRequest,
) -> Tuple[Optional[ExceptionRecord], Optional[str]]:
    """Applies a human review decision to an exception with strict audit tracking."""
    action_key = req.action.upper()
    if action_key not in ALLOWED_HITL_TRANSITIONS:
        return None, f"Invalid resolution action: '{req.action}'. Allowed: {list(ALLOWED_HITL_TRANSITIONS.keys())}"

    target_status = ALLOWED_HITL_TRANSITIONS[action_key]

    record = db.query(ExceptionRecord).filter(ExceptionRecord.id == exception_id).with_for_update().first()
    if not record:
        return None, f"Exception with id '{exception_id}' not found."

    if record.status in TERMINAL_STATUSES:
        status_val = record.status.value if hasattr(record.status, "value") else str(record.status)
        return None, f"Exception '{exception_id}' is already closed with status '{status_val}'."

    old_status_val = record.status.value if hasattr(record.status, "value") else str(record.status)
    new_status_val = target_status.value if hasattr(target_status, "value") else str(target_status)

    now = datetime.now(timezone.utc)
    record.status = target_status

    # Consolidate audit details dictionary
    audit_details = {
        "reason": req.reason,
        "adjustment_amount": req.adjustment_amount,
    }
    if req.metadata:
        audit_details.update(req.metadata)

    audit_entry = AuditLog(
        target_type="EXCEPTION",
        target_id=record.id,
        action=action_key,
        actor=req.reviewer_id,
        old_status=old_status_val,
        new_status=new_status_val,
        details=audit_details,
        timestamp=now,
    )
    db.add(audit_entry)
    db.commit()
    db.refresh(record)

    return record, None


def get_exception_detail(db: Session, exception_id: str) -> Optional[ExceptionDetailResponse]:
    """Retrieves an exception record along with its immutable audit history."""
    record = db.query(ExceptionRecord).filter(ExceptionRecord.id == exception_id).first()
    if not record:
        return None

    audit_rows = (
        db.query(AuditLog)
        .filter(AuditLog.target_type == "EXCEPTION", AuditLog.target_id == exception_id)
        .order_by(AuditLog.timestamp.asc())
        .all()
    )

    trail = []
    for row in audit_rows:
        raw_details = row.details
        if isinstance(raw_details, str):
            try:
                raw_details = json.loads(raw_details)
            except Exception:
                raw_details = {"raw": raw_details}
        elif not isinstance(raw_details, dict):
            raw_details = {}

        trail.append(
            AuditLogRecord(
                id=row.id,
                target_type=row.target_type,
                target_id=row.target_id,
                action=row.action,
                actor=row.actor,
                old_status=row.old_status,
                new_status=row.new_status,
                details=raw_details,
                timestamp=row.timestamp,
            )
        )

    return ExceptionDetailResponse(
        id=record.id,
        tenant_id=record.tenant_id,
        job_id=record.job_id,
        type=record.type,
        status=ExceptionStatus.OPEN if (record.status == "NEW" or str(record.status) == "NEW") else record.status,
        entity_id=record.entity_id,
        entity_type=record.entity_type,
        reason=record.reason,
        amount=record.amount,
        audit_trail=trail,
    )
