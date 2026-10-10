"""Pydantic schemas for reconciliation jobs, exceptions, and approval flows."""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from app.domain.enums import (
    JobStatus,
    MatchStatus,
    MatchMethod,
    ExceptionType,
    ExceptionStatus,
    ApprovalStatus,
    ApprovalDecision,
)


# --- 1. Reconciliation Job Schemas ---

class JobCreateResponse(BaseModel):
    """Immediate response returned after uploading files (202 Accepted)."""
    job_id: str = Field(..., description="Unique reconciliation job identifier")
    status: JobStatus = Field(default=JobStatus.QUEUED)
    status_url: str = Field(..., description="Polling endpoint to monitor job progress")
    created_at: datetime


class JobStatusResponse(BaseModel):
    """Detailed status of a reconciliation job."""
    job_id: str
    status: JobStatus
    tenant_id: str
    total_invoices: int = 0
    total_payments: int = 0
    matched_count: int = 0
    exception_count: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_summary: Optional[str] = None


# --- 2. Exception Schemas ---

class ExceptionItemResponse(BaseModel):
    """Schema representing an identified financial discrepancy."""
    exception_id: str
    job_id: str
    match_id: Optional[str] = None
    invoice_id: Optional[str] = None
    payment_id: Optional[str] = None
    exception_type: ExceptionType
    status: ExceptionStatus
    difference_amount: Optional[Decimal] = None
    currency: Optional[str] = None
    rule_id: Optional[str] = None
    evidence: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


# --- 3. Approval Schemas ---

class ApprovalDecisionRequest(BaseModel):
    """Request payload when a human reviewer submits a decision."""
    decision: ApprovalDecision
    reason: str = Field(..., min_length=3, max_length=500, description="Justification for the decision")
    version: int = Field(..., ge=1, description="Optimistic locking version counter")


class ApprovalResponse(BaseModel):
    """Response returned after an approval decision is recorded."""
    approval_id: str
    exception_id: str
    job_id: str
    status: ApprovalStatus
    decided_by: str
    decided_at: datetime
    decision: ApprovalDecision
    decision_reason: str
