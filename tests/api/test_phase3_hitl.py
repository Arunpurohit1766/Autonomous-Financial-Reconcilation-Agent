"""Test suite for Phase 3: Human-in-the-Loop Exception Resolution & Audit Trails."""

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

from app.main import app
from app.domain.enums import ExceptionType, ExceptionStatus, JobStatus
from app.persistence.database import SessionLocal, Base, engine
from app.persistence.models import ExceptionRecord, ReconciliationJob, Tenant
from app.persistence.models.audit import AuditLog

client = TestClient(app)
AUTH_HEADERS = {"Authorization": "Bearer key_admin_alpha"}
TENANT_ID = "tenant_alpha"


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    # Clean up test-specific audit logs and exceptions
    db.query(AuditLog).filter(AuditLog.target_id.in_(["exc-001", "exc-002"])).delete(synchronize_session=False)
    db.query(ExceptionRecord).filter(ExceptionRecord.id.in_(["exc-001", "exc-002"])).delete(synchronize_session=False)
    db.commit()

    t = db.query(Tenant).filter(Tenant.id == TENANT_ID).first()
    if not t:
        db.add(Tenant(id=TENANT_ID, name=TENANT_ID))
        db.commit()
    db.close()
    yield


def test_hitl_resolve_exception_lifecycle():
    db = SessionLocal()
    # 1. Create and commit parent job first
    job = ReconciliationJob(
        id="job-hitl-001",
        tenant_id=TENANT_ID,
        status=JobStatus.COMPLETED_WITH_EXCEPTIONS,
        total_invoices=10,
        total_payments=10,
        matched_count=8,
        exception_count=2,
    )
    db.merge(job)
    db.commit()

    # 2. Create exception linked to parent job
    exc = ExceptionRecord(
        id="exc-001",
        tenant_id=TENANT_ID,
        job_id="job-hitl-001",
        type=ExceptionType.AMOUNT_MISMATCH,
        status=ExceptionStatus.OPEN,
        entity_id="inv-101",
        entity_type="INVOICE",
        reason="Difference of $25.00 detected",
        amount=Decimal("25.00"),
    )
    db.merge(exc)
    db.commit()
    db.close()

    # 3. Query pending exceptions via API
    resp = client.get("/api/v1/hitl/exceptions?job_id=job-hitl-001", headers=AUTH_HEADERS)
    assert resp.status_code == 200, f"Failed listing exceptions: {resp.text}"
    items = resp.json()
    assert len(items) >= 1
    assert any(i["id"] == "exc-001" for i in items)

    # 4. Resolve the exception (OVERRIDE -> RESOLVED)
    resolution_payload = {
        "action": "OVERRIDE",
        "reviewer_id": "auditor_sarah@company.com",
        "reason": "Authorized $25 credit waiver approved by finance lead.",
        "adjustment_amount": "25.00",
        "metadata": {"ticket_ref": "FIN-9821"},
    }
    resolve_resp = client.post("/api/v1/hitl/exceptions/exc-001/resolve", json=resolution_payload, headers=AUTH_HEADERS)
    assert resolve_resp.status_code == 200, f"Failed resolving: {resolve_resp.text}"
    detail = resolve_resp.json()
    assert detail["status"] in ("RESOLVED", ExceptionStatus.RESOLVED.value)
    assert len(detail["audit_trail"]) == 1

    audit_entry = detail["audit_trail"][0]
    assert audit_entry["action"] == "OVERRIDE"
    assert audit_entry["actor"] in ("usr_admin_alpha", "auditor_sarah@company.com")
    assert audit_entry["details"]["ticket_ref"] == "FIN-9821"

    # 5. Duplicate resolution returns 400 Bad Request
    dup_resp = client.post("/api/v1/hitl/exceptions/exc-001/resolve", json=resolution_payload, headers=AUTH_HEADERS)
    assert dup_resp.status_code == 400
    assert "already closed" in dup_resp.json()["detail"].lower()

    # 6. Check dedicated audit query endpoint
    audit_resp = client.get("/api/v1/hitl/audit/exc-001", headers=AUTH_HEADERS)
    assert audit_resp.status_code == 200
    records = audit_resp.json()
    assert len(records) == 1
    assert records[0]["target_id"] == "exc-001"


def test_hitl_reject_action():
    db = SessionLocal()
    # 1. Create and commit parent job first
    job = ReconciliationJob(
        id="job-hitl-002",
        tenant_id=TENANT_ID,
        status=JobStatus.COMPLETED_WITH_EXCEPTIONS,
        total_invoices=5,
        total_payments=5,
        matched_count=4,
        exception_count=1,
    )
    db.merge(job)
    db.commit()

    # 2. Create exception linked to parent job
    exc = ExceptionRecord(
        id="exc-002",
        tenant_id=TENANT_ID,
        job_id="job-hitl-002",
        type=ExceptionType.INVOICE_NOT_FOUND,
        status=ExceptionStatus.OPEN,
        entity_id="pmt-202",
        entity_type="PAYMENT",
        reason="Unknown payment ID received",
        amount=Decimal("100.00"),
    )
    db.merge(exc)
    db.commit()
    db.close()

    # 3. Reject exception (REJECT -> REJECTED)
    payload = {
        "action": "REJECT",
        "reviewer_id": "supervisor_john@company.com",
        "reason": "Invalid payment claims rejected.",
    }
    resolve_resp = client.post("/api/v1/hitl/exceptions/exc-002/resolve", json=payload, headers=AUTH_HEADERS)
    assert resolve_resp.status_code == 200, f"Failed rejecting: {resolve_resp.text}"
    assert resolve_resp.json()["status"] in ("REJECTED", ExceptionStatus.REJECTED.value)
