"""FastAPI endpoints for Human-in-the-Loop review actions and audit querying."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.schemas.hitl import ExceptionResolutionRequest, ExceptionDetailResponse, AuditLogRecord
from app.persistence.models.all_models import ExceptionRecord
from app.persistence.models.audit import AuditLog
from app.services.hitl_service import resolve_exception, get_exception_detail

router = APIRouter(prefix="/hitl", tags=["Human-In-The-Loop"])


@router.get("/exceptions", response_model=List[dict])
def list_pending_exceptions(
    job_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    """List exceptions requiring review, optionally filtered by job_id."""
    query = db.query(ExceptionRecord)
    if job_id:
        query = query.filter(ExceptionRecord.job_id == job_id)
    records = query.order_by(ExceptionRecord.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "job_id": r.job_id,
            "exception_type": r.exception_type.value if hasattr(r.exception_type, "value") else str(r.exception_type),
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "invoice_id": r.invoice_id,
            "payment_id": r.payment_id,
            "reason": r.reason,
            "reviewer": r.reviewer,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        }
        for r in records
    ]


@router.get("/exceptions/{exception_id}", response_model=ExceptionDetailResponse)
def get_exception(
    exception_id: str,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    """Fetch complete detail and audit trail for a specific exception."""
    res = get_exception_detail(db, exception_id)
    if not res:
        raise HTTPException(status_code=404, detail="Exception not found")
    return res


@router.post("/exceptions/{exception_id}/resolve", response_model=ExceptionDetailResponse)
def apply_resolution(
    exception_id: str,
    req: ExceptionResolutionRequest,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    """Submit a reviewer decision (APPROVE, REJECT, OVERRIDE, INVESTIGATE)."""
    record, err = resolve_exception(db, exception_id, req)
    if err:
        if "not found" in err.lower():
            raise HTTPException(status_code=404, detail=err)
        raise HTTPException(status_code=400, detail=err)

    res = get_exception_detail(db, exception_id)
    return res


@router.get("/audit/{target_id}", response_model=List[AuditLogRecord])
def get_audit_trail(
    target_id: str,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    """Retrieve immutable audit logs for any job or exception."""
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.target_id == target_id)
        .order_by(AuditLog.timestamp.asc())
        .all()
    )
    return [
        AuditLogRecord(
            id=l.id,
            target_type=l.target_type,
            target_id=l.target_id,
            action=l.action,
            actor=l.actor,
            old_status=l.old_status,
            new_status=l.new_status,
            details=l.details or {},
            timestamp=l.timestamp,
        )
        for l in logs
    ]
