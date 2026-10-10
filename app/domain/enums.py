"""Domain enums representing all explicit states across the reconciliation lifecycle."""

from enum import Enum


class JobStatus(str, Enum):
    """Lifecycle status of a reconciliation batch job."""
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_EXCEPTIONS = "COMPLETED_WITH_EXCEPTIONS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class MatchMethod(str, Enum):
    """The exact strategy used to pair an invoice with a payment."""
    EXACT_INVOICE_ID = "EXACT_INVOICE_ID"
    PROVIDER_TRANSACTION_ID = "PROVIDER_TRANSACTION_ID"
    COMPOSITE_FALLBACK = "COMPOSITE_FALLBACK"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class MatchStatus(str, Enum):
    """Outcome of attempting to match an invoice against payment records."""
    MATCHED = "MATCHED"
    NO_MATCH = "NO_MATCH"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    DUPLICATE_SOURCE_RECORD = "DUPLICATE_SOURCE_RECORD"
    INVALID_SOURCE_RECORD = "INVALID_SOURCE_RECORD"


class ExceptionType(str, Enum):
    """Specific category of a financial discrepancy."""
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    TAX_MISMATCH = "TAX_MISMATCH"
    FEE_MISMATCH = "FEE_MISMATCH"
    PAYMENT_NOT_FOUND = "PAYMENT_NOT_FOUND"
    INVOICE_NOT_FOUND = "INVOICE_NOT_FOUND"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    DATA_QUALITY_ERROR = "DATA_QUALITY_ERROR"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ExceptionStatus(str, Enum):
    """Lifecycle state of an identified exception."""
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"


class ApprovalDecision(str, Enum):
    """Decision submitted by an authorized human reviewer."""
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_INFO = "REQUEST_INFO"


class ApprovalStatus(str, Enum):
    """State of an approval request record."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class UserRole(str, Enum):
    """Role-based access control roles."""
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    ADMIN = "admin"
