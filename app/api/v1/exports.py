"""FastAPI endpoints for exporting CSV reconciliation reports and JSON compliance packages with strict tenant scoping."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_auth
from app.core.security import AuthContext
from app.persistence.models import ReconciliationJob
from app.reporting.export_service import (
    generate_reconciliation_csv_export,
    generate_full_compliance_audit_json,
)

router = APIRouter(prefix="/exports", tags=["Exports & Compliance"])


@router.get("/{job_id}/csv")
def export_job_csv(
    job_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    """Download full reconciliation results as CSV with strict tenant isolation."""
    job = db.query(ReconciliationJob).filter(
        ReconciliationJob.id == job_id,
        ReconciliationJob.tenant_id == auth.tenant_id,
    ).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reconciliation job '{job_id}' not found",
        )

    csv_data, err = generate_reconciliation_csv_export(db, job_id)
    if err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err)

    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=reconciliation_{job_id}.csv"},
    )


@router.get("/{job_id}/compliance-json")
def export_job_compliance_json(
    job_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    """Download full SOX compliance audit JSON package with strict tenant isolation."""
    job = db.query(ReconciliationJob).filter(
        ReconciliationJob.id == job_id,
        ReconciliationJob.tenant_id == auth.tenant_id,
    ).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reconciliation job '{job_id}' not found",
        )

    pkg, err = generate_full_compliance_audit_json(db, job_id)
    if err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err)

    return pkg
