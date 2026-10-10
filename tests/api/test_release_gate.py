"""Comprehensive Release Gate verification tests covering tenant isolation, RBAC, spoofing, and safe ingestion."""

import io
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from sqlalchemy.exc import OperationalError

from app.main import app
from app.domain.enums import ExceptionType, ExceptionStatus, JobStatus, UserRole
from app.persistence.database import SessionLocal, Base, engine
from app.persistence.models import ExceptionRecord, ReconciliationJob, Tenant, MatchRecord
from app.persistence.models.audit import AuditLog

client = TestClient(app)

# Credentials configured in security.py for testing
ALPHA_HEADERS = {"Authorization": "Bearer key_admin_alpha"}   # tenant_alpha, ADMIN
BETA_HEADERS = {"Authorization": "Bearer key_operator_beta"}   # tenant_beta, OPERATOR


@pytest.fixture(autouse=True)
def setup_tenants_and_data():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    # Ensure both tenants exist
    for tid in ["tenant_alpha", "tenant_beta"]:
        t = db.query(Tenant).filter(Tenant.id == tid).first()
        if not t:
            db.add(Tenant(id=tid, name=tid))
    db.commit()

    # Seed an Alpha job & exception
    job_alpha = ReconciliationJob(
        id="job-gate-alpha",
        tenant_id="tenant_alpha",
        status=JobStatus.COMPLETED_WITH_EXCEPTIONS,
        total_invoices=5,
        total_payments=5,
        matched_count=4,
        exception_count=1,
    )
    db.merge(job_alpha)
    db.commit()

    exc_alpha = ExceptionRecord(
        id="exc-gate-alpha",
        tenant_id="tenant_alpha",
        job_id="job-gate-alpha",
        type=ExceptionType.AMOUNT_MISMATCH,
        status=ExceptionStatus.OPEN,
        entity_id="inv-gate-1",
        entity_type="INVOICE",
        reason="Difference of $10.00",
        amount=Decimal("10.00"),
    )
    db.merge(exc_alpha)
    db.commit()
    db.close()
    yield


def test_cross_tenant_isolation_on_all_endpoints():
    """Tenant Beta MUST NOT access Tenant Alpha's job, exceptions, exports, or AI explanations."""
    # 1. Job status endpoint
    resp = client.get("/api/v1/reconciliations/job-gate-alpha", headers=BETA_HEADERS)
    assert resp.status_code == 404

    # 2. Exceptions query
    resp = client.get("/api/v1/hitl/exceptions/exc-gate-alpha", headers=BETA_HEADERS)
    assert resp.status_code == 404

    # 3. CSV export
    resp = client.get("/api/v1/exports/job-gate-alpha/csv", headers=BETA_HEADERS)
    assert resp.status_code == 404

    # 4. Compliance JSON export
    resp = client.get("/api/v1/exports/job-gate-alpha/compliance-json", headers=BETA_HEADERS)
    assert resp.status_code == 404

    # 5. AI explanation endpoint
    resp = client.post("/api/v1/hitl/exceptions/exc-gate-alpha/explain", json={}, headers=BETA_HEADERS)
    assert resp.status_code == 404


def test_rbac_operator_cannot_resolve_exception():
    """An OPERATOR role is forbidden from resolving an exception (HTTP 403)."""
    payload = {
        "action": "OVERRIDE",
        "reviewer_id": "operator_beta",
        "reason": "Attempting unauthorized override",
    }
    resp = client.post("/api/v1/hitl/exceptions/exc-gate-alpha/resolve", json=payload, headers=BETA_HEADERS)
    assert resp.status_code == 403
    assert "not authorized" in resp.json()["detail"].lower()


def test_empty_and_header_only_uploads_rejected():
    """Empty files (0 bytes) or header-only CSVs must be rejected with HTTP 400."""
    empty_file = ("empty.csv", io.BytesIO(b""), "text/csv")
    valid_file = ("valid.csv", io.BytesIO(b"id,amount\n1,100\n"), "text/csv")
    
    # 1. Empty upload
    resp = client.post(
        "/api/v1/reconciliations",
        files={"invoice_file": empty_file, "payment_file": valid_file},
        headers=ALPHA_HEADERS,
    )
    assert resp.status_code == 400
    assert "empty file" in resp.json()["detail"].lower()

    # 2. Header-only upload (no data rows)
    header_only = ("header_only.csv", io.BytesIO(b"invoice_id,amount,date\n"), "text/csv")
    resp2 = client.post(
        "/api/v1/reconciliations",
        files={"invoice_file": header_only, "payment_file": valid_file},
        headers=ALPHA_HEADERS,
    )
    assert resp2.status_code == 400
    assert "header-only" in resp2.json()["detail"].lower()


def test_readiness_probe_returns_503_on_db_failure():
    """When database connectivity fails, /health/ready must return HTTP 503."""
    with patch("sqlalchemy.orm.Session.execute", side_effect=OperationalError("connection failed", None, None)):
        resp = client.get("/health/ready")
        assert resp.status_code == 503
        assert "database unavailable" in resp.json()["detail"].lower()
