"""Durable background job recovery strictly coordinating with lease expirations and fencing."""

from datetime import datetime, timedelta
from typing import List
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.domain.enums import JobStatus
from app.persistence.models import ReconciliationJob


def recover_stuck_jobs(db: Session) -> List[str]:
    """
    Recover jobs stuck in PROCESSING whose lease has expired, or jobs without
    a lease started over 30 minutes ago.
    """
    now = datetime.utcnow()
    stale_threshold = now - timedelta(minutes=30)

    expired_jobs = (
        db.query(ReconciliationJob)
        .filter(
            ReconciliationJob.status == JobStatus.PROCESSING.value,
            or_(
                (ReconciliationJob.lease_expires_at != None) & (ReconciliationJob.lease_expires_at < now),
                (ReconciliationJob.lease_expires_at == None) & (ReconciliationJob.started_at < stale_threshold),
            )
        )
        .all()
    )

    recovered_ids = []
    for job in expired_jobs:
        job.status = JobStatus.FAILED.value
        job.error_summary = "Job execution lease expired (worker stalled, crashed, or was fenced out)."
        job.completed_at = now
        job.lease_token = None
        recovered_ids.append(job.id)

    if recovered_ids:
        db.commit()

    return recovered_ids
