"""Comprehensive tests covering lease fencing, recovery races, and durable job polling."""

import asyncio
from datetime import datetime, timedelta
import threading
import time
import pytest

from app.domain.enums import JobStatus
from app.persistence.database import SessionLocal, Base, engine
from app.persistence.models import ReconciliationJob, Tenant, MatchRecord, ExceptionRecord, ReportRecord
from app.workflows.runner import (
    claim_job,
    heartbeat_lease,
    verify_worker_ownership,
    run_reconciliation_workflow,
    process_all_queued_jobs,
    background_worker_poll_loop,
)
from app.workflows.recovery import recover_stuck_jobs


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if not db.query(Tenant).filter(Tenant.id == "tenant_fencing_test").first():
            db.add(Tenant(id="tenant_fencing_test", name="Fencing Tenant"))
            db.commit()


def test_stale_worker_cannot_commit_after_losing_lease():
    """1. A worker that loses its lease token cannot commit results or overwrite status."""
    with SessionLocal() as db:
        job = ReconciliationJob(
            tenant_id="tenant_fencing_test",
            status=JobStatus.QUEUED.value,
        )
        db.add(job)
        db.commit()
        job_id = job.id

        # Worker 1 claims job
        worker_1_token = claim_job(db, job_id, lease_duration_seconds=10)
        assert worker_1_token is not None

        # Simulate Worker 1 stalling: Recovery runs after lease expires and fences out Worker 1
        job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
        recovered = recover_stuck_jobs(db)
        assert job_id in recovered

        # Worker 1 ownership verification fails
        assert not verify_worker_ownership(db, job_id, worker_1_token)

        # Worker 1 attempting to execute with stale token is aborted without writes
        run_reconciliation_workflow(job_id, worker_token=worker_1_token)

        # Confirm job is still FAILED from recovery, and no reports/matches were written
        refreshed = db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id).first()
        assert refreshed.status == JobStatus.FAILED.value
        assert db.query(ReportRecord).filter(ReportRecord.job_id == job_id).count() == 0


def test_concurrent_claim_prevents_dual_processing():
    """2. Two concurrent workers attempting the same job cannot both claim or process it."""
    with SessionLocal() as db:
        job = ReconciliationJob(
            tenant_id="tenant_fencing_test",
            status=JobStatus.QUEUED.value,
        )
        db.add(job)
        db.commit()
        job_id = job.id

    tokens = []
    def worker_attempt():
        with SessionLocal() as session:
            tok = claim_job(session, job_id, lease_duration_seconds=30)
            if tok:
                tokens.append(tok)

    t1 = threading.Thread(target=worker_attempt)
    t2 = threading.Thread(target=worker_attempt)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one thread claimed the job
    assert len(tokens) == 1


def test_recovery_racing_with_completion():
    """3. Recovery running against an active job with a valid lease does not touch the job."""
    with SessionLocal() as db:
        future_lease = datetime.utcnow() + timedelta(minutes=5)
        job = ReconciliationJob(
            tenant_id="tenant_fencing_test",
            status=JobStatus.PROCESSING.value,
            lease_expires_at=future_lease,
            lease_token="valid_worker_token",
        )
        db.add(job)
        db.commit()
        job_id = job.id

        recovered = recover_stuck_jobs(db)
        assert job_id not in recovered

        refreshed = db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id).first()
        assert refreshed.status == JobStatus.PROCESSING.value
        assert refreshed.lease_token == "valid_worker_token"


@pytest.mark.asyncio
async def test_queued_job_discovered_by_worker_loop_without_restart():
    """4. A queued job is picked up by the continuous polling loop without restarting."""
    with SessionLocal() as db:
        job = ReconciliationJob(
            tenant_id="tenant_fencing_test",
            status=JobStatus.QUEUED.value,
            # No files assigned: will fail during workflow, proving it was picked up and processed
        )
        db.add(job)
        db.commit()
        job_id = job.id

    stop_event = asyncio.Event()
    loop_task = asyncio.create_task(background_worker_poll_loop(poll_interval_seconds=0.1, stop_event=stop_event))

    # Give loop a brief moment to process the queued job
    await asyncio.sleep(0.4)
    stop_event.set()
    await loop_task

    with SessionLocal() as db:
        refreshed = db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id).first()
        # Job must have been transitioned out of QUEUED
        assert refreshed.status in [JobStatus.COMPLETED.value, JobStatus.FAILED.value]


def test_abandoned_job_can_be_recovered_safely():
    """5. An abandoned job with an expired lease is safely recovered to FAILED."""
    with SessionLocal() as db:
        expired = datetime.utcnow() - timedelta(minutes=2)
        job = ReconciliationJob(
            tenant_id="tenant_fencing_test",
            status=JobStatus.PROCESSING.value,
            lease_expires_at=expired,
            lease_token="abandoned_token",
        )
        db.add(job)
        db.commit()
        job_id = job.id

        recovered = recover_stuck_jobs(db)
        assert job_id in recovered

        refreshed = db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id).first()
        assert refreshed.status == JobStatus.FAILED.value
        assert refreshed.lease_token is None
