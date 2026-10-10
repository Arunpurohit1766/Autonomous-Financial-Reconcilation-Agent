"""Unit tests verifying durable QUEUED job discovery and lease-safe recovery."""

from datetime import datetime, timedelta
from app.persistence.database import SessionLocal
from app.persistence.models import ReconciliationJob, Tenant, SourceFile
from app.workflows.recovery import recover_stuck_jobs
from app.workflows.runner import process_all_queued_jobs
from app.integrations.storage.local import storage_adapter


def test_persisted_queued_jobs_discovered_and_processed():
    with open("tests/fixtures/clean_invoices.csv", "rb") as f:
        inv_bytes = f.read()
    with open("tests/fixtures/clean_payments.csv", "rb") as f:
        pmt_bytes = f.read()

    with SessionLocal() as db:
        if not db.query(Tenant).filter(Tenant.id == "tenant_durable").first():
            db.add(Tenant(id="tenant_durable", name="Durable Tenant"))
            db.commit()

        job = ReconciliationJob(tenant_id="tenant_durable", status="QUEUED")
        db.add(job)
        db.flush()

        inv_meta = storage_adapter.save_file(inv_bytes, "invoices.csv")
        pmt_meta = storage_adapter.save_file(pmt_bytes, "payments.csv")

        f_inv = SourceFile(tenant_id="tenant_durable", job_id=job.id, file_kind="invoice", original_filename="invoices.csv", storage_key=inv_meta["storage_key"], sha256=inv_meta["sha256"], size_bytes=inv_meta["size_bytes"])
        f_pmt = SourceFile(tenant_id="tenant_durable", job_id=job.id, file_kind="payment", original_filename="payments.csv", storage_key=pmt_meta["storage_key"], sha256=pmt_meta["sha256"], size_bytes=pmt_meta["size_bytes"])
        db.add(f_inv)
        db.add(f_pmt)
        db.flush()

        job.invoice_file_id = f_inv.id
        job.payment_file_id = f_pmt.id
        db.commit()
        job_id = job.id

        processed = process_all_queued_jobs(db)
        assert job_id in processed

        refreshed = db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id).first()
        assert refreshed.status == "COMPLETED"


def test_active_job_with_valid_lease_not_failed():
    with SessionLocal() as db:
        if not db.query(Tenant).filter(Tenant.id == "tenant_lease_test").first():
            db.add(Tenant(id="tenant_lease_test", name="Lease Tenant"))
            db.commit()

        future_lease = datetime.utcnow() + timedelta(minutes=5)
        job = ReconciliationJob(
            tenant_id="tenant_lease_test",
            status="PROCESSING",
            started_at=datetime.utcnow(),
            lease_expires_at=future_lease,
        )
        db.add(job)
        db.commit()
        active_id = job.id

        recovered = recover_stuck_jobs(db)
        assert active_id not in recovered

        refreshed = db.query(ReconciliationJob).filter(ReconciliationJob.id == active_id).first()
        assert refreshed.status == "PROCESSING"


def test_expired_lease_is_recovered():
    with SessionLocal() as db:
        if not db.query(Tenant).filter(Tenant.id == "tenant_lease_test").first():
            db.add(Tenant(id="tenant_lease_test", name="Lease Tenant"))
            db.commit()

        past_lease = datetime.utcnow() - timedelta(minutes=5)
        job = ReconciliationJob(
            tenant_id="tenant_lease_test",
            status="PROCESSING",
            started_at=datetime.utcnow() - timedelta(minutes=10),
            lease_expires_at=past_lease,
        )
        db.add(job)
        db.commit()
        expired_id = job.id

        recovered = recover_stuck_jobs(db)
        assert expired_id in recovered

        refreshed = db.query(ReconciliationJob).filter(ReconciliationJob.id == expired_id).first()
        assert refreshed.status == "FAILED"
        assert "lease expired" in refreshed.error_summary
