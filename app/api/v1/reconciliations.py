"""API endpoints for uploading, polling, inspecting exceptions, and downloading reports with verified auth."""

from typing import Optional, List
from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Depends,
    HTTPException,
    BackgroundTasks,
    status,
    Query,
)
from sqlalchemy.orm import Session

from app.core.config import settings
from app.dependencies import get_db, get_current_auth
from app.core.security import AuthContext
from app.persistence.models import (
    Tenant,
    ReconciliationJob,
    SourceFile,
    ExceptionRecord,
    ReportRecord,
)
from app.schemas.reconciliation import (
    JobCreateResponse,
    JobStatusResponse,
    ExceptionItemResponse,
)
from app.integrations.storage.local import storage_adapter
from app.workflows.runner import execute_reconciliation_job
from app.persistence.database import SessionLocal

router = APIRouter(prefix="/reconciliations", tags=["Reconciliations"])


def ensure_tenant(db: Session, tenant_id: str) -> None:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        db.add(Tenant(id=tenant_id, name=tenant_id))
        db.flush()


def run_job_in_background(job_id: str):
    db: Session = SessionLocal()
    try:
        execute_reconciliation_job(job_id, db)
    finally:
        db.close()


@router.post("", response_model=JobCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_reconciliation_job(
    background_tasks: BackgroundTasks,
    invoice_file: UploadFile = File(..., description="Invoices CSV file"),
    payment_file: UploadFile = File(..., description="Payments CSV file"),
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    """Upload invoice and payment files to launch a background reconciliation job."""
    for uf in (invoice_file, payment_file):
        if not uf.filename.lower().endswith(".csv"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only CSV files supported. Received '{uf.filename}'",
            )

    inv_bytes = await invoice_file.read()
    pmt_bytes = await payment_file.read()

    # Reject empty files (0 bytes or only whitespace)
    if len(inv_bytes.strip()) == 0 or len(pmt_bytes.strip()) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file uploaded. Both invoice and payment CSV files must contain valid data.",
        )

    # Reject header-only files (less than 2 lines)
    inv_lines = [l for l in inv_bytes.decode("utf-8", errors="ignore").splitlines() if l.strip()]
    pmt_lines = [l for l in pmt_bytes.decode("utf-8", errors="ignore").splitlines() if l.strip()]
    if len(inv_lines) < 2 or len(pmt_lines) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Header-only or insufficient CSV uploaded. Files must contain at least one data row.",
        )

    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024

    if len(inv_bytes) > max_bytes or len(pmt_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed upload size of {settings.MAX_UPLOAD_MB} MB",
        )

    # Tenant is strictly taken from verified AuthContext
    ensure_tenant(db, auth.tenant_id)

    job = ReconciliationJob(tenant_id=auth.tenant_id, status="QUEUED")
    db.add(job)
    db.flush()

    inv_meta = storage_adapter.save_file(inv_bytes, invoice_file.filename)
    pmt_meta = storage_adapter.save_file(pmt_bytes, payment_file.filename)

    inv_source = SourceFile(
        tenant_id=auth.tenant_id,
        job_id=job.id,
        file_kind="invoice",
        original_filename=invoice_file.filename,
        storage_key=inv_meta["storage_key"],
        sha256=inv_meta["sha256"],
        size_bytes=inv_meta["size_bytes"],
    )
    pmt_source = SourceFile(
        tenant_id=auth.tenant_id,
        job_id=job.id,
        file_kind="payment",
        original_filename=payment_file.filename,
        storage_key=pmt_meta["storage_key"],
        sha256=pmt_meta["sha256"],
        size_bytes=pmt_meta["size_bytes"],
    )
    db.add(inv_source)
    db.add(pmt_source)
    db.flush()

    job.invoice_file_id = inv_source.id
    job.payment_file_id = pmt_source.id
    db.commit()

    background_tasks.add_task(run_job_in_background, job.id)

    return JobCreateResponse(
        job_id=job.id,
        status=job.status,
        status_url=f"/api/v1/reconciliations/{job.id}",
        created_at=job.created_at,
    )


@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job_status(
    job_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    """Retrieve job progress with strict tenant isolation."""
    job = db.query(ReconciliationJob).filter(
        ReconciliationJob.id == job_id,
        ReconciliationJob.tenant_id == auth.tenant_id,
    ).first()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reconciliation job '{job_id}' not found",
        )

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        tenant_id=job.tenant_id,
        total_invoices=job.total_invoices,
        total_payments=job.total_payments,
        matched_count=job.matched_count,
        exception_count=job.exception_count,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_summary=job.error_summary,
    )


@router.get("/{job_id}/exceptions", response_model=List[ExceptionItemResponse])
def get_job_exceptions(
    job_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    """Retrieve paginated exceptions scoped strictly to caller's tenant."""
    job = db.query(ReconciliationJob).filter(
        ReconciliationJob.id == job_id,
        ReconciliationJob.tenant_id == auth.tenant_id,
    ).first()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reconciliation job '{job_id}' not found",
        )

    records = db.query(ExceptionRecord).filter(
        ExceptionRecord.job_id == job_id,
        ExceptionRecord.tenant_id == auth.tenant_id,
    ).order_by(ExceptionRecord.created_at.desc()).offset(offset).limit(limit).all()

    res = []
    for r in records:
        inv_id = r.entity_id if r.entity_type == "INVOICE" else None
        pmt_id = r.entity_id if r.entity_type == "PAYMENT" else None
        exc_type = r.type if hasattr(r.type, "value") else str(r.type)
        raw_st = r.status.value if hasattr(r.status, "value") else str(r.status)
        status_val = "OPEN" if raw_st == "NEW" else raw_st
        created_dt = r.created_at or datetime.now(timezone.utc)
        
        res.append(
            ExceptionItemResponse(
                exception_id=r.id,
                job_id=r.job_id,
                invoice_id=inv_id,
                payment_id=pmt_id,
                exception_type=exc_type,
                status=status_val,
                difference_amount=r.amount,
                currency="USD",
                rule_id="RULE-DISCREPANCY",
                evidence={"reason": r.reason} if r.reason else {},
                created_at=created_dt,
                updated_at=created_dt,
            )
        )
    return res


@router.get("/{job_id}/report")
def get_job_report(
    job_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    """Retrieve audit JSON report scoped strictly to caller's tenant."""
    report = db.query(ReportRecord).filter(
        ReportRecord.job_id == job_id,
        ReportRecord.tenant_id == auth.tenant_id,
    ).first()

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report for reconciliation job '{job_id}' not found or still processing",
        )

    return report.report_payload
