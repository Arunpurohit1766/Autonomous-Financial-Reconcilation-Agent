"""FastAPI endpoints for Human-in-the-Loop review actions, audit querying, and Agentic AI explanations with strict tenant isolation and RBAC."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_auth
from app.core.security import AuthContext
from app.domain.enums import UserRole
from app.schemas.hitl import ExceptionResolutionRequest, ExceptionDetailResponse, AuditLogRecord
from app.schemas.agent import ExplainExceptionRequest, ExplainExceptionResponse
from app.persistence.models.all_models import ExceptionRecord, ReconciliationJob
from app.persistence.models.audit import AuditLog
from app.services.hitl_service import resolve_exception, get_exception_detail
from app.services.agent_service import explain_exception_record

router = APIRouter(prefix="/hitl", tags=["Human-In-The-Loop"])


@router.get("/exceptions", response_model=List[dict])
def list_pending_exceptions(
    job_id: Optional[str] = None,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    """List exceptions requiring review, strictly scoped to caller's tenant."""
    query = db.query(ExceptionRecord).filter(ExceptionRecord.tenant_id == auth.tenant_id)
    if job_id:
        query = query.filter(ExceptionRecord.job_id == job_id)
    records = query.order_by(ExceptionRecord.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "tenant_id": r.tenant_id,
            "job_id": r.job_id,
            "type": r.type.value if hasattr(r.type, "value") else str(r.type),
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "entity_id": r.entity_id,
            "entity_type": r.entity_type,
            "reason": r.reason,
            "amount": float(r.amount) if r.amount is not None else None,
        }
        for r in records
    ]


@router.get("/exceptions/{exception_id}", response_model=ExceptionDetailResponse)
def get_exception(
    exception_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    """Fetch complete detail and audit trail for a specific exception scoped to caller's tenant."""
    record = db.query(ExceptionRecord).filter(
        ExceptionRecord.id == exception_id,
        ExceptionRecord.tenant_id == auth.tenant_id,
    ).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exception not found")

    res = get_exception_detail(db, exception_id)
    return res


@router.post("/exceptions/{exception_id}/resolve", response_model=ExceptionDetailResponse)
def apply_resolution(
    exception_id: str,
    req: ExceptionResolutionRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    """
    Submit a reviewer decision (APPROVE, REJECT, OVERRIDE, INVESTIGATE).
    Enforces RBAC (only REVIEWER or ADMIN allowed) and derives reviewer actor strictly from auth.user_id.
    """
    # Enforce RBAC
    if auth.role not in (UserRole.REVIEWER, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{auth.role.value if hasattr(auth.role, 'value') else auth.role}' is not authorized to resolve exceptions. Reviewer or Admin role required.",
        )

    # Verify exception belongs to authenticated tenant
    record = db.query(ExceptionRecord).filter(
        ExceptionRecord.id == exception_id,
        ExceptionRecord.tenant_id == auth.tenant_id,
    ).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exception not found")

    # Strictly use verified authenticated user_id as actor to prevent spoofing
    authenticated_actor = auth.user_id or f"user-{auth.tenant_id}"
    req.reviewer_id = authenticated_actor

    record, err = resolve_exception(db, exception_id, req)
    if err:
        if "not found" in err.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)

    res = get_exception_detail(db, exception_id)
    return res


@router.post("/exceptions/{exception_id}/explain", response_model=ExplainExceptionResponse)
def explain_exception(
    exception_id: str,
    req: ExplainExceptionRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    """Generate agentic AI root-cause diagnosis for an exception scoped to caller's tenant."""
    record = db.query(ExceptionRecord).filter(
        ExceptionRecord.id == exception_id,
        ExceptionRecord.tenant_id == auth.tenant_id,
    ).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exception not found")

    actor_id = auth.user_id or f"user-{auth.tenant_id}"
    resp, err = explain_exception_record(db, exception_id, req, actor_id=actor_id)
    if err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err)
    return resp


@router.get("/audit/{target_id}", response_model=List[AuditLogRecord])
def get_audit_trail(
    target_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    """
    Retrieve immutable audit logs.
    Verifies that the target resource (job or exception) belongs to the caller's tenant.
    """
    # Check if target is a job owned by tenant
    is_owned = db.query(ReconciliationJob).filter(
        ReconciliationJob.id == target_id,
        ReconciliationJob.tenant_id == auth.tenant_id,
    ).first() is not None

    if not is_owned:
        # Check if target is an exception owned by tenant
        is_owned = db.query(ExceptionRecord).filter(
            ExceptionRecord.id == target_id,
            ExceptionRecord.tenant_id == auth.tenant_id,
        ).first() is not None

    if not is_owned:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource '{target_id}' not found",
        )

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
