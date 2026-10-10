"""Pydantic schema definitions for invoice records with arithmetic validation."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from app.domain.money import to_money, validate_invoice_totals


class InvoiceRecordCreate(BaseModel):
    """Schema for validating ingested raw invoice records."""
    invoice_id: str = Field(..., min_length=1, description="Unique invoice identifier")
    customer_reference: Optional[str] = Field(None, description="Customer identifier or name")
    issued_at: datetime = Field(..., description="Date/timestamp invoice was issued")
    due_at: Optional[datetime] = Field(None, description="Due date for payment")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 3-letter currency code")
    subtotal: Decimal = Field(..., description="Invoice subtotal before tax and discount")
    tax_amount: Decimal = Field(default=Decimal("0.00"), description="Tax amount")
    discount_amount: Decimal = Field(default=Decimal("0.00"), description="Discount amount")
    total_amount: Decimal = Field(..., description="Final invoice total")
    payment_reference: Optional[str] = Field(None, description="Payment reference ID")
    source_row_number: int = Field(..., ge=1, description="Row index in original file")

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("subtotal", "tax_amount", "discount_amount", "total_amount")
    @classmethod
    def quantize_amounts(cls, v: Decimal) -> Decimal:
        return to_money(v)

    @field_validator("total_amount")
    @classmethod
    def validate_positive_total(cls, v: Decimal) -> Decimal:
        if v < Decimal("0.00"):
            raise ValueError("Invoice total_amount cannot be negative")
        return v

    @model_validator(mode="after")
    def verify_arithmetic_consistency(self) -> "InvoiceRecordCreate":
        if not validate_invoice_totals(
            self.subtotal, self.tax_amount, self.discount_amount, self.total_amount
        ):
            expected = self.subtotal + self.tax_amount - self.discount_amount
            raise ValueError(
                f"Arithmetic mismatch: subtotal ({self.subtotal}) + tax ({self.tax_amount}) - "
                f"discount ({self.discount_amount}) != total ({self.total_amount}). Expected {expected}"
            )
        return self


class InvoiceRecordResponse(InvoiceRecordCreate):
    """Schema for returning an invoice record via API."""
    id: str
    job_id: str
    created_at: datetime
