"""FastAPI application entrypoint with continuous durable worker background dispatch."""

import asyncio
from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.api.router import api_router
from app.dependencies import get_db, require_role
from app.domain.enums import UserRole
from app.persistence.database import engine, Base
from app.workflows.recovery import recover_stuck_jobs
from app.workflows.runner import process_all_queued_jobs, background_worker_poll_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    
    with next(get_db()) as db:
        recover_stuck_jobs(db)

    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(background_worker_poll_loop(poll_interval_seconds=1.0, stop_event=stop_event))

    yield

    stop_event.set()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Autonomous Financial Reconciliation Agent (AFRA)",
    version="1.0.0",
    description="Enterprise Multi-Tenant Financial Reconciliation Engine",
    lifespan=lifespan,
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
@app.get("/health/live", tags=["Health"])
def health_live():
    return {"status": "ok", "service": "AFRA"}


@app.get("/health/ready", tags=["Health"])
def health_ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database unavailable: {str(e)}",
        )


@app.post("/api/v1/jobs/recover", tags=["Admin Operations"])
def trigger_manual_recovery(
    db: Session = Depends(get_db),
    auth=Depends(require_role(UserRole.ADMIN.value)),
):
    """Admin-only operational endpoint to force recovery of stuck jobs."""
    recovered_job_ids = recover_stuck_jobs(db)
    return {"status": "success", "recovered_job_ids": recovered_job_ids}
