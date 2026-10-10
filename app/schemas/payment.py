"""Pydantic schema definitions for payment records with money quantization."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from app.domain.money import to_money


class PaymentRecordCreate(BaseModel):
    """Schema for validating ingested raw payment records."""
    payment_id: str = Field(..., min_length=1, description="Unique payment identifier")
    invoice_reference: Optional[str] = Field(None, description="Invoice reference ID")
    customer_reference: Optional[str] = Field(None, description="Customer reference")
    paid_at: datetime = Field(..., description="Timestamp of payment execution")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 3-letter currency code")
    gross_amount: Decimal = Field(..., description="Total amount paid before deductions")
    fee_amount: Decimal = Field(default=Decimal("0.00"), description="Processing fee charged by provider")
    net_amount: Optional[Decimal] = Field(None, description="Net settlement amount")
    provider_status: str = Field(default="succeeded", description="Status reported by provider")
    source_row_number: int = Field(..., ge=1, description="Row index in original file")

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("gross_amount", "fee_amount")
    @classmethod
    def quantize_amounts(cls, v: Decimal) -> Decimal:
        return to_money(v)

    @field_validator("net_amount")
    @classmethod
    def quantize_optional_net(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        return to_money(v) if v is not None else None

    @field_validator("gross_amount")
    @classmethod
    def validate_positive_gross(cls, v: Decimal) -> Decimal:
        if v < Decimal("0.00"):
            raise ValueError("Payment gross_amount cannot be negative")
        return v

    @model_validator(mode="after")
    def check_net_consistency(self) -> "PaymentRecordCreate":
        if self.net_amount is not None:
            expected_net = to_money(self.gross_amount - self.fee_amount)
            if self.net_amount != expected_net:
                raise ValueError(
                    f"Net amount mismatch: gross ({self.gross_amount}) - fee ({self.fee_amount}) "
                    f"!= net ({self.net_amount}). Expected {expected_net}"
                )
        return self


class PaymentRecordResponse(PaymentRecordCreate):
    """Schema for returning a payment record via API."""
    id: str
    job_id: str
    created_at: datetime
