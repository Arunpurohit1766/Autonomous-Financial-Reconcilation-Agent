"""Pydantic schemas for LLM-assisted and heuristic discrepancy root-cause analysis."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class DiscrepancyExplanation(BaseModel):
    root_cause_category: str = Field(
        ...,
        description="Category: 'TAX_ROUNDING', 'BANK_WIRE_FEE', 'FX_RATE_FLUCTUATION', 'PARTIAL_PAYMENT', 'TIMING_DIFFERENCE', 'UNKNOWN'"
    )
    summary: str = Field(..., description="High-level summary of the discrepancy")
    detailed_reasoning: str = Field(..., description="Step-by-step financial reasoning")
    suggested_action: str = Field(
        ...,
        description="Recommended advisory action: 'APPROVE_ADJUSTMENT', 'REQUEST_SUPPLEMENTAL_PAYMENT', 'REJECT_AND_ESCALATE', 'INVESTIGATE_FURTHER'"
    )
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    evidence_citations: List[str] = Field(default_factory=list, description="Specific recorded data points supporting the deduction")
    is_authoritative: bool = Field(False, description="Flag indicating output is exploratory/advisory and cannot settle ledgers")

    model_config = ConfigDict(from_attributes=True)


class ExplainExceptionRequest(BaseModel):
    additional_context: Optional[str] = Field(None, description="Optional manual context provided by the reviewer")
    force_refresh: bool = Field(False, description="Whether to bypass cached explanation and re-analyze")


class ExplainExceptionResponse(BaseModel):
    exception_id: str
    job_id: str
    explanation: DiscrepancyExplanation
    provider: str
    model: str
    disclaimer: str = Field(
        "Advisory AI/heuristic analysis only. Cannot alter financial reconciliation records or ledger states without human approval.",
        description="Legal disclaimer on agent authority"
    )

    model_config = ConfigDict(from_attributes=True)
