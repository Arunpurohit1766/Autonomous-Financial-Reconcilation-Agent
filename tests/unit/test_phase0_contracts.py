"""Comprehensive Phase 0 test suite verifying financial math, RBAC, transitions, and schemas."""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError

from app.domain.enums import (
    JobStatus,
    ExceptionStatus,
    ApprovalStatus,
    ApprovalDecision,
    UserRole,
)
from app.domain.transitions import (
    validate_job_transition,
    validate_exception_transition,
    validate_approval_transition,
    validate_approval_authorization,
    resolve_decision_transitions,
    InvalidStateTransitionError,
    UnauthorizedApprovalError,
)
from app.domain.money import to_money, validate_invoice_totals
from app.schemas.invoice import InvoiceRecordCreate
from app.schemas.payment import PaymentRecordCreate


# --- 1. Decimal & Financial Math Tests ---

def test_decimal_penny_quantization():
    """Verify ROUND_HALF_UP quantization to 2 decimal places."""
    assert to_money("100.004") == Decimal("100.00")
    assert to_money("100.005") == Decimal("100.01")
    assert to_money(100.1) == Decimal("100.10")


def test_invoice_total_consistency():
    """Verify subtotal + tax - discount == total."""
    assert validate_invoice_totals(
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("18.00"),
        discount_amount=Decimal("5.00"),
        total_amount=Decimal("113.00"),
    ) is True

    # Inconsistent total
    assert validate_invoice_totals(
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("18.00"),
        discount_amount=Decimal("0.00"),
        total_amount=Decimal("119.00"),
    ) is False


# --- 2. Schema Validation & Edge Cases ---

def test_invoice_schema_arithmetic_mismatch_fails():
    """Validation fails if invoice totals do not add up."""
    with pytest.raises(ValidationError):
        InvoiceRecordCreate(
            invoice_id="INV-BAD-MATH",
            issued_at=datetime.now(timezone.utc),
            currency="USD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("10.00"),
            discount_amount=Decimal("0.00"),
            total_amount=Decimal("105.00"),  # Expected 110.00
            source_row_number=1,
        )


def test_payment_net_consistency_fails():
    """Validation fails if gross - fee != net."""
    with pytest.raises(ValidationError):
        PaymentRecordCreate(
            payment_id="PAY-BAD-NET",
            paid_at=datetime.now(timezone.utc),
            currency="USD",
            gross_amount=Decimal("100.00"),
            fee_amount=Decimal("3.00"),
            net_amount=Decimal("95.00"),  # Expected 97.00
            source_row_number=1,
        )


def test_negative_monetary_values_rejected():
    """Negative values must raise validation error."""
    with pytest.raises(ValidationError):
        InvoiceRecordCreate(
            invoice_id="INV-NEG",
            issued_at=datetime.now(timezone.utc),
            currency="USD",
            subtotal=Decimal("-10.00"),
            tax_amount=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            total_amount=Decimal("-10.00"),
            source_row_number=1,
        )


# --- 3. State Transitions & Idempotency ---

def test_valid_job_lifecycle():
    validate_job_transition(JobStatus.QUEUED, JobStatus.PROCESSING)
    validate_job_transition(JobStatus.PROCESSING, JobStatus.WAITING_FOR_REVIEW)
    validate_job_transition(JobStatus.WAITING_FOR_REVIEW, JobStatus.COMPLETED)


def test_illegal_terminal_transitions():
    with pytest.raises(InvalidStateTransitionError):
        validate_job_transition(JobStatus.COMPLETED, JobStatus.PROCESSING)

    with pytest.raises(InvalidStateTransitionError):
        validate_approval_transition(ApprovalStatus.APPROVED, ApprovalStatus.REJECTED)


# --- 4. REQUEST_INFO & Approval Lifecycle ---

def test_request_info_lifecycle():
    """Verify REQUEST_INFO keeps approval PENDING and re-routes Exception to INVESTIGATING."""
    app_status, exc_status = resolve_decision_transitions(ApprovalDecision.REQUEST_INFO)
    assert app_status == ApprovalStatus.PENDING
    assert exc_status == ExceptionStatus.INVESTIGATING

    # Verify exception transition from PENDING_REVIEW -> INVESTIGATING is valid
    validate_exception_transition(ExceptionStatus.PENDING_REVIEW, exc_status)


def test_approval_decision_routing():
    """Verify APPROVE and REJECT transitions."""
    app_s, exc_s = resolve_decision_transitions(ApprovalDecision.APPROVE)
    assert app_s == ApprovalStatus.APPROVED
    assert exc_s == ExceptionStatus.APPROVED

    app_r, exc_r = resolve_decision_transitions(ApprovalDecision.REJECT)
    assert app_r == ApprovalStatus.REJECTED
    assert exc_r == ExceptionStatus.REJECTED


# --- 5. Authorization & RBAC ---

def test_rbac_authorization():
    """Verify operators cannot approve; reviewers and admins can."""
    with pytest.raises(UnauthorizedApprovalError):
        validate_approval_authorization(UserRole.OPERATOR)

    # Should succeed without error
    validate_approval_authorization(UserRole.REVIEWER)
    validate_approval_authorization(UserRole.ADMIN)
