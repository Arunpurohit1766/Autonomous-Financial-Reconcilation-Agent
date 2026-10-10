"""JSON reconciliation report generator with transparent discrepancy metrics and parsing errors."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from app.domain.enums import ExceptionType
from app.domain.money import to_money
from app.reconciliation.calculator import ReconciliationItem


class ReconciliationSummary(BaseModel):
    """High-level summary metrics of a reconciliation run."""
    total_records_evaluated: int
    total_reconciled: int
    total_exceptions: int
    reconciliation_rate_percent: float
    total_variance_amount: Decimal = Field(
        default=Decimal("0.00"),
        description="Sum of price/fee variances on matched records"
    )
    total_unmatched_invoice_amount: Decimal = Field(
        default=Decimal("0.00"),
        description="Sum of invoices where payment was not found"
    )
    total_orphan_payment_amount: Decimal = Field(
        default=Decimal("0.00"),
        description="Sum of payments received without a matching invoice"
    )
    exceptions_by_type: Dict[str, int] = Field(default_factory=dict)


class ReconciliationReport(BaseModel):
    """Complete audit report payload."""
    report_id: str
    job_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: ReconciliationSummary
    items: List[ReconciliationItem]


def generate_json_report(
    job_id: str,
    items: List[ReconciliationItem],
    parsing_error_items: Optional[List[ReconciliationItem]] = None,
) -> ReconciliationReport:
    """Compile evaluated reconciliation items and raw CSV parsing errors into an audit report."""
    all_items = list(items) + (list(parsing_error_items) if parsing_error_items else [])

    total_evaluated = len(all_items)
    reconciled_items = [i for i in all_items if i.is_reconciled]
    exception_items = [i for i in all_items if not i.is_reconciled]

    total_reconciled = len(reconciled_items)
    total_exceptions = len(exception_items)

    rate = round((total_reconciled / total_evaluated * 100), 2) if total_evaluated > 0 else 0.0

    total_variance = Decimal("0.00")
    total_unmatched_invoices = Decimal("0.00")
    total_orphan_payments = Decimal("0.00")
    exceptions_by_type: Dict[str, int] = {}

    for exc in exception_items:
        if exc.exception_type:
            key = exc.exception_type.value
            exceptions_by_type[key] = exceptions_by_type.get(key, 0) + 1

        amt = exc.difference_amount or Decimal("0.00")
        if exc.exception_type == ExceptionType.PAYMENT_NOT_FOUND:
            total_unmatched_invoices += amt
        elif exc.exception_type == ExceptionType.INVOICE_NOT_FOUND:
            total_orphan_payments += amt
        elif exc.exception_type in (ExceptionType.AMOUNT_MISMATCH, ExceptionType.FEE_MISMATCH):
            total_variance += amt

    summary = ReconciliationSummary(
        total_records_evaluated=total_evaluated,
        total_reconciled=total_reconciled,
        total_exceptions=total_exceptions,
        reconciliation_rate_percent=rate,
        total_variance_amount=to_money(total_variance),
        total_unmatched_invoice_amount=to_money(total_unmatched_invoices),
        total_orphan_payment_amount=to_money(total_orphan_payments),
        exceptions_by_type=exceptions_by_type,
    )

    report_id = f"REP-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{job_id[:8]}"

    return ReconciliationReport(
        report_id=report_id,
        job_id=job_id,
        summary=summary,
        items=all_items,
    )
