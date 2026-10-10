"""Test suite for Phase 5: Production Export & Compliance Packages."""

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

from app.main import app
from app.domain.enums import ExceptionType, ExceptionStatus, JobStatus
from app.persistence.database import SessionLocal, Base, engine
from app.persistence.models import (
    ExceptionRecord,
    ReconciliationJob,
    Tenant,
    MatchRecord,
    ReportRecord,
)
from app.persistence.models.audit import AuditLog

client = TestClient(app)
AUTH_HEADERS = {"Authorization": "Bearer key_admin_alpha"}
TENANT_ID = "tenant_alpha"


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    # Clean up test records
    db.query(AuditLog).filter(AuditLog.target_id.in_(["job-p5-001", "exc-p5-001"])).delete(synchronize_session=False)
    db.query(ExceptionRecord).filter(ExceptionRecord.id == "exc-p5-001").delete(synchronize_session=False)
    db.query(MatchRecord).filter(MatchRecord.job_id == "job-p5-001").delete(synchronize_session=False)
    db.query(ReportRecord).filter(ReportRecord.job_id == "job-p5-001").delete(synchronize_session=False)
    db.query(ReconciliationJob).filter(ReconciliationJob.id == "job-p5-001").delete(synchronize_session=False)
    db.commit()

    t = db.query(Tenant).filter(Tenant.id == TENANT_ID).first()
    if not t:
        db.add(Tenant(id=TENANT_ID, name=TENANT_ID))
        db.commit()
    db.close()
    yield


def test_export_reconciliation_csv_flow():
    db = SessionLocal()
    # 1. Seed job and commit
    job = ReconciliationJob(
        id="job-p5-001",
        tenant_id=TENANT_ID,
        status=JobStatus.COMPLETED_WITH_EXCEPTIONS,
        total_invoices=10,
        total_payments=10,
        matched_count=9,
        exception_count=1,
    )
    db.merge(job)
    db.commit()

    # 2. Seed match record with required columns
    match_rec = MatchRecord(
        id="match-p5-001",
        tenant_id=TENANT_ID,
        job_id="job-p5-001",
        invoice_id="inv-101",
        payment_id="pmt-101",
        method="EXACT",
        status="MATCHED",
        confidence=1.0,
        variance_amount=Decimal("0.00"),
    )
    db.merge(match_rec)

    # 3. Seed exception record
    exc_rec = ExceptionRecord(
        id="exc-p5-001",
        tenant_id=TENANT_ID,
        job_id="job-p5-001",
        type=ExceptionType.AMOUNT_MISMATCH,
        status=ExceptionStatus.RESOLVED,
        entity_id="inv-102",
        entity_type="INVOICE",
        reason="Discrepancy of $5.00 resolved by reviewer",
        amount=Decimal("5.00"),
    )
    db.merge(exc_rec)

    # 4. Seed audit log
    audit_log = AuditLog(
        target_type="EXCEPTION",
        target_id="exc-p5-001",
        action="OVERRIDE",
        actor="compliance_officer@fin.corp",
        old_status="OPEN",
        new_status="RESOLVED",
        details={"reason": "Approved waiver"},
    )
    db.add(audit_log)
    db.commit()
    db.close()

    # Test CSV Export Endpoint
    resp = client.get("/api/v1/exports/job-p5-001/csv", headers=AUTH_HEADERS)
    assert resp.status_code == 200, f"CSV export failed: {resp.text}"
    assert "text/csv" in resp.headers["content-type"]
    content = resp.text
    assert "=== RECONCILIATION SUMMARY ===" in content
    assert "job-p5-001" in content
    assert "inv-101" in content
    assert "exc-p5-001" in content

    # Test Compliance JSON Export Endpoint
    json_resp = client.get("/api/v1/exports/job-p5-001/compliance-json", headers=AUTH_HEADERS)
    assert json_resp.status_code == 200, f"JSON export failed: {json_resp.text}"
    pkg = json_resp.json()
    assert pkg["export_metadata"]["compliance_standard"] == "SOX-404-FINANCIAL-AUDIT"
    assert pkg["job"]["id"] == "job-p5-001"
    assert len(pkg["audit_ledger_trail"]) >= 1
    assert pkg["audit_ledger_trail"][0]["actor"] == "compliance_officer@fin.corp"
