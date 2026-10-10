"""API integration tests verifying bearer auth, tenant isolation, and reporting."""

import io
from fastapi.testclient import TestClient
import pytest
from app.main import app

client = TestClient(app)


def test_health_endpoints():
    live = client.get("/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"

    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "connected"


def test_unauthenticated_request_rejected():
    """Requests without valid Bearer credentials are rejected with 401."""
    res = client.get("/api/v1/reconciliations/some-job")
    assert res.status_code == 401


def test_reconciliation_clean_fixtures_api_flow():
    """Upload clean CSVs with verified auth header."""
    with open("tests/fixtures/clean_invoices.csv", "rb") as f_inv:
        inv_content = f_inv.read()
    with open("tests/fixtures/clean_payments.csv", "rb") as f_pmt:
        pmt_content = f_pmt.read()

    files = {
        "invoice_file": ("clean_invoices.csv", io.BytesIO(inv_content), "text/csv"),
        "payment_file": ("clean_payments.csv", io.BytesIO(pmt_content), "text/csv"),
    }
    headers = {"Authorization": "Bearer key_admin_alpha"}

    # Upload files -> 202 Accepted
    response = client.post("/api/v1/reconciliations", files=files, headers=headers)
    assert response.status_code == 202
    data = response.json()
    job_id = data["job_id"]

    # Poll Status
    status_resp = client.get(f"/api/v1/reconciliations/{job_id}", headers=headers)
    assert status_resp.status_code == 200
    job_status = status_resp.json()
    assert job_status["status"] == "COMPLETED"
    assert job_status["total_invoices"] == 3
    assert job_status["matched_count"] == 3
    assert job_status["exception_count"] == 0

    # Fetch Report
    report_resp = client.get(f"/api/v1/reconciliations/{job_id}/report", headers=headers)
    assert report_resp.status_code == 200
    report_data = report_resp.json()
    assert report_data["summary"]["reconciliation_rate_percent"] == 100.0


def test_tenant_isolation_with_auth_tokens():
    """Caller with Tenant Alpha credentials cannot access Tenant Beta resources."""
    with open("tests/fixtures/clean_invoices.csv", "rb") as f_inv:
        inv_content = f_inv.read()
    with open("tests/fixtures/clean_payments.csv", "rb") as f_pmt:
        pmt_content = f_pmt.read()

    files = {
        "invoice_file": ("clean_invoices.csv", io.BytesIO(inv_content), "text/csv"),
        "payment_file": ("clean_payments.csv", io.BytesIO(pmt_content), "text/csv"),
    }
    # Create under Tenant Alpha
    res = client.post("/api/v1/reconciliations", files=files, headers={"Authorization": "Bearer key_admin_alpha"})
    job_id = res.json()["job_id"]

    # Attempt access using Tenant Beta token
    get_res = client.get(f"/api/v1/reconciliations/{job_id}", headers={"Authorization": "Bearer key_admin_beta"})
    assert get_res.status_code == 404
