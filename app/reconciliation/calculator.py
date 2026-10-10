"""Deterministic reconciliation calculator computing discrepancies, tolerances, ambiguity, and duplicates."""

from decimal import Decimal
from typing import Optional, Dict, Any, List
from pydantic import BaseModel

from app.domain.enums import ExceptionType, MatchStatus
from app.domain.money import to_money
from app.reconciliation.matcher import MatchResult
from app.schemas.payment import PaymentRecordCreate


class ReconciliationItem(BaseModel):
    """The financial reconciliation evaluation of a matched pair, orphan, or duplicate record."""
    invoice_id: Optional[str] = None
    payment_id: Optional[str] = None
    is_reconciled: bool
    exception_type: Optional[ExceptionType] = None
    difference_amount: Optional[Decimal] = None
    currency: Optional[str] = None
    rule_id: str
    evidence: Dict[str, Any] = {}


class ReconciliationCalculator:
    """Computes exact monetary differences and assigns exception classifications."""

    def __init__(self, tolerance: Decimal = Decimal("0.01")):
        self.tolerance = to_money(tolerance)

    def evaluate_pair(self, match: MatchResult) -> ReconciliationItem:
        """Evaluate a matched, unmatched, or ambiguous invoice-payment pair."""
        inv = match.invoice
        pmt = match.payment

        # Case 1: Ambiguous Match (multiple candidates existed)
        if match.status == MatchStatus.AMBIGUOUS_MATCH:
            return ReconciliationItem(
                invoice_id=inv.invoice_id if inv else None,
                payment_id=None,
                is_reconciled=False,
                exception_type=ExceptionType.AMBIGUOUS_MATCH,
                difference_amount=inv.total_amount if inv else None,
                currency=inv.currency if inv else None,
                rule_id="RULE-007-AMBIGUOUS-MATCH",
                evidence=match.evidence,
            )

        # Case 2: Invoice has no payment (NO_MATCH)
        if pmt is None:
            return ReconciliationItem(
                invoice_id=inv.invoice_id if inv else None,
                is_reconciled=False,
                exception_type=ExceptionType.PAYMENT_NOT_FOUND,
                difference_amount=inv.total_amount if inv else None,
                currency=inv.currency if inv else None,
                rule_id="RULE-001-PAYMENT-NOT-FOUND",
                evidence={"reason": "Invoice exists in billing system but no corresponding payment found."},
            )

        # Case 3: Currency mismatch
        if inv.currency != pmt.currency:
            return ReconciliationItem(
                invoice_id=inv.invoice_id,
                payment_id=pmt.payment_id,
                is_reconciled=False,
                exception_type=ExceptionType.CURRENCY_MISMATCH,
                difference_amount=Decimal("0.00"),
                currency=f"{inv.currency} vs {pmt.currency}",
                rule_id="RULE-002-CURRENCY-MISMATCH",
                evidence={
                    "invoice_currency": inv.currency,
                    "payment_currency": pmt.currency,
                    "reason": "Currencies do not match. Automated reconciliation requires identical currencies.",
                },
            )

        # Case 4: Amount calculation
        inv_total = to_money(inv.total_amount)
        pmt_gross = to_money(pmt.gross_amount)
        diff = to_money(abs(inv_total - pmt_gross))

        # Sub-case 4a: Exact match or within rounding tolerance
        if diff <= self.tolerance:
            return ReconciliationItem(
                invoice_id=inv.invoice_id,
                payment_id=pmt.payment_id,
                is_reconciled=True,
                difference_amount=diff,
                currency=inv.currency,
                rule_id="RULE-003-EXACT-OR-TOLERANCE-MATCH",
                evidence={
                    "invoice_total": str(inv_total),
                    "payment_gross": str(pmt_gross),
                    "difference": str(diff),
                },
            )

        # Sub-case 4b: Gateway Fee mismatch
        if pmt.fee_amount > Decimal("0.00") and to_money(pmt.gross_amount + pmt.fee_amount) == inv_total:
            return ReconciliationItem(
                invoice_id=inv.invoice_id,
                payment_id=pmt.payment_id,
                is_reconciled=False,
                exception_type=ExceptionType.FEE_MISMATCH,
                difference_amount=to_money(pmt.fee_amount),
                currency=inv.currency,
                rule_id="RULE-004-GATEWAY-FEE-MISMATCH",
                evidence={
                    "invoice_total": str(inv_total),
                    "payment_gross": str(pmt_gross),
                    "fee_deducted": str(pmt.fee_amount),
                    "reason": "Payment received is less than invoice total by the exact recorded gateway fee.",
                },
            )

        # Sub-case 4c: General Amount Mismatch
        return ReconciliationItem(
            invoice_id=inv.invoice_id,
            payment_id=pmt.payment_id,
            is_reconciled=False,
            exception_type=ExceptionType.AMOUNT_MISMATCH,
            difference_amount=diff,
            currency=inv.currency,
            rule_id="RULE-005-AMOUNT-MISMATCH",
            evidence={
                "invoice_total": str(inv_total),
                "payment_gross": str(pmt_gross),
                "difference": str(diff),
                "reason": f"Discrepancy of {diff} {inv.currency} exceeds tolerance of {self.tolerance}.",
            },
        )

    def evaluate_orphan_payment(self, pmt: PaymentRecordCreate) -> ReconciliationItem:
        """Handle valid unreferenced payment that matches no invoice."""
        return ReconciliationItem(
            payment_id=pmt.payment_id,
            is_reconciled=False,
            exception_type=ExceptionType.INVOICE_NOT_FOUND,
            difference_amount=to_money(pmt.gross_amount),
            currency=pmt.currency,
            rule_id="RULE-006-INVOICE-NOT-FOUND",
            evidence={
                "reason": "Payment received but no matching invoice exists in internal records.",
                "source_row_number": pmt.source_row_number,
            },
        )

    def evaluate_duplicate_payment(self, pmt: PaymentRecordCreate) -> ReconciliationItem:
        """Handle corrupted/duplicate payment record explicitly as a data quality error."""
        return ReconciliationItem(
            payment_id=pmt.payment_id,
            is_reconciled=False,
            exception_type=ExceptionType.DATA_QUALITY_ERROR,
            difference_amount=to_money(pmt.gross_amount),
            currency=pmt.currency,
            rule_id="RULE-008-DUPLICATE-PAYMENT-ID",
            evidence={
                "reason": f"Duplicate payment identifier '{pmt.payment_id}' detected in input source.",
                "source_row_number": pmt.source_row_number,
            },
        )

    def reconcile(
        self,
        match_results: List[MatchResult],
        unmatched_valid_payments: List[PaymentRecordCreate],
        duplicate_payments: Optional[List[PaymentRecordCreate]] = None,
    ) -> List[ReconciliationItem]:
        """Run full evaluation across matches, valid orphans, and duplicates."""
        items: List[ReconciliationItem] = []
        for m in match_results:
            items.append(self.evaluate_pair(m))
        for p in unmatched_valid_payments:
            items.append(self.evaluate_orphan_payment(p))
        if duplicate_payments:
            for d in duplicate_payments:
                items.append(self.evaluate_duplicate_payment(d))
        return items
