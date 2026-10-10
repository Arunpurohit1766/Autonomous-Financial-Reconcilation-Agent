"""Schemas for Human-in-the-Loop (HITL) exception workflows and audit trails."""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, ConfigDict

from app.domain.enums import ExceptionStatus, ExceptionType


class ExceptionResolutionRequest(BaseModel):
    action: str = Field(..., description="Action to take: 'APPROVE', 'REJECT', 'OVERRIDE', 'INVESTIGATE'")
    reviewer_id: str = Field(..., min_length=1, description="ID or email of the reviewer")
    reason: str = Field(..., min_length=3, description="Audit justification for this action")
    adjustment_amount: Optional[str] = Field(None, description="Decimal adjustment amount if overriding")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Custom review metadata")


class AuditLogRecord(BaseModel):
    id: str
    target_type: str
    target_id: str
    action: str
    actor: str
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


class ExceptionDetailResponse(BaseModel):
    id: str
    tenant_id: Optional[str] = None
    job_id: str
    type: ExceptionType
    status: ExceptionStatus
    entity_id: Optional[str] = None
    entity_type: Optional[str] = None
    reason: Optional[str] = None
    amount: Optional[Any] = None
    audit_trail: List[AuditLogRecord] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
