"""Test suite for Phase 4: Agentic AI Discrepancy Explanation and Root-Cause Diagnosis."""

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

from app.main import app
from app.domain.enums import ExceptionType, ExceptionStatus, JobStatus
from app.persistence.database import SessionLocal, Base, engine
from app.persistence.models import ExceptionRecord, ReconciliationJob, Tenant
from app.persistence.models.audit import AuditLog
from app.agent.engine import FinancialExplanationAgent

client = TestClient(app)
AUTH_HEADERS = {"Authorization": "Bearer key_admin_alpha"}
TENANT_ID = "tenant_alpha"


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    # Clean up test rows
    db.query(AuditLog).filter(AuditLog.target_id == "exc-agent-wire-fee").delete(synchronize_session=False)
    db.query(ExceptionRecord).filter(ExceptionRecord.id == "exc-agent-wire-fee").delete(synchronize_session=False)
    db.commit()

    t = db.query(Tenant).filter(Tenant.id == TENANT_ID).first()
    if not t:
        db.add(Tenant(id=TENANT_ID, name=TENANT_ID))
        db.commit()
    db.close()
    yield


def test_agent_engine_tax_rounding_rule():
    agent = FinancialExplanationAgent()
    exp = agent.analyze_discrepancy(
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        reason="Penny mismatch",
        amount=Decimal("0.05"),
        entity_id="inv-rounding-01",
        entity_type="INVOICE",
    )
    assert exp.root_cause_category == "TAX_ROUNDING"
    assert exp.suggested_action == "APPROVE_ADJUSTMENT"
    assert exp.confidence_score >= 0.90


def test_agent_engine_wire_fee_rule():
    agent = FinancialExplanationAgent()
    exp = agent.analyze_discrepancy(
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        reason="Deduction observed",
        amount=Decimal("25.00"),
        entity_id="pmt-fee-01",
        entity_type="PAYMENT",
    )
    assert exp.root_cause_category == "BANK_WIRE_FEE"
    assert exp.suggested_action == "APPROVE_ADJUSTMENT"
    assert exp.confidence_score >= 0.90


def test_api_explain_exception_endpoint():
    db = SessionLocal()
    # 1. Commit parent job FIRST so foreign key is satisfied
    job = ReconciliationJob(
        id="job-agent-001",
        tenant_id=TENANT_ID,
        status=JobStatus.COMPLETED_WITH_EXCEPTIONS,
        total_invoices=2,
        total_payments=2,
        matched_count=1,
        exception_count=1,
    )
    db.merge(job)
    db.commit()

    # 2. Insert and commit exception record
    exc = ExceptionRecord(
        id="exc-agent-wire-fee",
        tenant_id=TENANT_ID,
        job_id="job-agent-001",
        type=ExceptionType.AMOUNT_MISMATCH,
        status=ExceptionStatus.OPEN,
        entity_id="pmt-wire-fee",
        entity_type="PAYMENT",
        reason="Payment arrived short by standard fee",
        amount=Decimal("25.00"),
    )
    db.merge(exc)
    db.commit()
    db.close()

    # 3. Call AI explanation endpoint
    payload = {"additional_context": "Client confirmed SWIFT wire processing", "force_refresh": True}
    resp = client.post(
        "/api/v1/hitl/exceptions/exc-agent-wire-fee/explain",
        json=payload,
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200, f"Explain endpoint failed: {resp.text}"
    data = resp.json()
    assert data["exception_id"] == "exc-agent-wire-fee"
    assert data["explanation"]["root_cause_category"] == "BANK_WIRE_FEE"
    assert data["explanation"]["suggested_action"] == "APPROVE_ADJUSTMENT"
    assert len(data["explanation"]["evidence_citations"]) >= 1

    # 4. Verify audit trail recorded the AI analysis event
    audit_resp = client.get("/api/v1/hitl/audit/exc-agent-wire-fee", headers=AUTH_HEADERS)
    assert audit_resp.status_code == 200
    audits = audit_resp.json()
    assert any(a["action"] == "AI_EXPLANATION_GENERATED" for a in audits)
