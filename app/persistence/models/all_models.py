"""SQLAlchemy ORM models representing all core domain entities with lease & fencing tracking."""

from datetime import datetime
import uuid
from sqlalchemy import (
    Column, String, Integer, Numeric, DateTime, ForeignKey, Text, JSON, Boolean
)
from sqlalchemy.orm import relationship
from app.persistence.database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.utcnow()


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=utc_now)


class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    external_subject = Column(String(100), nullable=False)
    role = Column(String(20), nullable=False, default="operator")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)


class SourceFile(Base):
    __tablename__ = "source_files"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    job_id = Column(String(36), ForeignKey("reconciliation_jobs.id"), nullable=True, index=True)
    file_kind = Column(String(50), nullable=True)
    original_filename = Column(String(255), nullable=True)
    storage_key = Column(String(255), nullable=True)
    storage_path = Column(String(255), nullable=True)
    sha256 = Column(String(64), nullable=True)
    size_bytes = Column(Integer, nullable=True, default=0)
    file_type = Column(String(20), nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    uploaded_at = Column(DateTime, default=utc_now)


class ReconciliationJob(Base):
    __tablename__ = "reconciliation_jobs"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    status = Column(String(40), nullable=False, default="QUEUED", index=True)
    invoice_file_id = Column(String(36), ForeignKey("source_files.id"), nullable=True)
    payment_file_id = Column(String(36), ForeignKey("source_files.id"), nullable=True)
    error_summary = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    total_invoices = Column(Integer, nullable=False, default=0)
    total_payments = Column(Integer, nullable=False, default=0)
    matched_count = Column(Integer, nullable=False, default=0)
    exception_count = Column(Integer, nullable=False, default=0)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    lease_token = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime, default=utc_now)


class InvoiceRecord(Base):
    __tablename__ = "invoices"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    job_id = Column(String(36), ForeignKey("reconciliation_jobs.id"), nullable=False, index=True)
    invoice_id = Column(String(100), nullable=False)
    customer_id = Column(String(100), nullable=False)
    invoice_date = Column(String(10), nullable=False)
    due_date = Column(String(10), nullable=False)
    currency = Column(String(3), nullable=False)
    subtotal_amount = Column(Numeric(12, 2), nullable=False)
    tax_amount = Column(Numeric(12, 2), nullable=False)
    total_amount = Column(Numeric(12, 2), nullable=False)
    status = Column(String(20), nullable=False)
    created_at = Column(DateTime, default=utc_now)


class PaymentRecord(Base):
    __tablename__ = "payments"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    job_id = Column(String(36), ForeignKey("reconciliation_jobs.id"), nullable=False, index=True)
    payment_id = Column(String(100), nullable=False)
    payment_date = Column(String(10), nullable=False)
    settlement_date = Column(String(10), nullable=False)
    currency = Column(String(3), nullable=False)
    gross_amount = Column(Numeric(12, 2), nullable=False)
    fee_amount = Column(Numeric(12, 2), nullable=False)
    net_amount = Column(Numeric(12, 2), nullable=False)
    provider_transaction_id = Column(String(100), nullable=True)
    reference_id = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False)
    created_at = Column(DateTime, default=utc_now)


class MatchRecord(Base):
    __tablename__ = "matches"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    job_id = Column(String(36), ForeignKey("reconciliation_jobs.id"), nullable=False, index=True)
    invoice_id = Column(String(100), nullable=True)
    payment_id = Column(String(100), nullable=True)
    method = Column(String(30), nullable=False)
    status = Column(String(20), nullable=False)
    confidence = Column(Numeric(3, 2), nullable=False)
    variance_amount = Column(Numeric(12, 2), nullable=False, default=0.00)
    created_at = Column(DateTime, default=utc_now)


class ExceptionRecord(Base):
    __tablename__ = "exceptions"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    job_id = Column(String(36), ForeignKey("reconciliation_jobs.id"), nullable=False, index=True)
    type = Column(String(40), nullable=False)
    status = Column(String(20), nullable=False)
    entity_id = Column(String(100), nullable=False)
    entity_type = Column(String(20), nullable=False)
    reason = Column(Text, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False, default=0.00)
    created_at = Column(DateTime, default=utc_now)


class ReportRecord(Base):
    __tablename__ = "reports"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    job_id = Column(String(36), ForeignKey("reconciliation_jobs.id"), nullable=False, unique=True)
    payload = Column(JSON, nullable=False)
    @property
    def report_payload(self):
        return self.payload
    generated_at = Column(DateTime, default=utc_now)
