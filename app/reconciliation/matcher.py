"""Deterministic 3-tier matching engine with duplicate detection and ambiguity guards."""

from datetime import timedelta
from typing import List, Dict, Tuple, Optional, Any
from pydantic import BaseModel

from app.domain.enums import MatchMethod, MatchStatus
from app.schemas.invoice import InvoiceRecordCreate
from app.schemas.payment import PaymentRecordCreate


class MatchResult(BaseModel):
    """Result of pairing an invoice with a payment or recording an unmatch/ambiguity."""
    invoice: Optional[InvoiceRecordCreate] = None
    payment: Optional[PaymentRecordCreate] = None
    status: MatchStatus
    method: Optional[MatchMethod] = None
    candidate_score: float = 1.0
    evidence: Dict[str, Any] = {}


class DeterministicMatcher:
    """Executes multi-tier deterministic matching rules with duplicate isolation."""

    def __init__(self, date_window_days: int = 3):
        self.date_window = timedelta(days=date_window_days)

    def match(
        self,
        invoices: List[InvoiceRecordCreate],
        payments: List[PaymentRecordCreate],
    ) -> Tuple[List[MatchResult], List[PaymentRecordCreate], List[PaymentRecordCreate]]:
        """
        Execute deterministic matching across all invoices and payments.
        Returns:
            (match_results, unmatched_valid_payments, duplicate_payments)
        """
        matched_results: List[MatchResult] = []
        matched_payment_ids = set()

        # Step 1: Detect duplicate payment IDs up front
        payment_counts: Dict[str, int] = {}
        for p in payments:
            payment_counts[p.payment_id] = payment_counts.get(p.payment_id, 0) + 1

        duplicate_payment_ids = {pid for pid, count in payment_counts.items() if count > 1}

        valid_payments: List[PaymentRecordCreate] = []
        duplicate_payments: List[PaymentRecordCreate] = []

        for p in payments:
            if p.payment_id in duplicate_payment_ids:
                duplicate_payments.append(p)
            else:
                valid_payments.append(p)

        # Index valid payments for safe lookup
        payments_by_invoice_ref: Dict[str, List[PaymentRecordCreate]] = {}
        payments_by_id: Dict[str, PaymentRecordCreate] = {}

        for p in valid_payments:
            if p.invoice_reference:
                payments_by_invoice_ref.setdefault(p.invoice_reference, []).append(p)
            payments_by_id[p.payment_id] = p

        # Step 2: Match Invoices
        for inv in invoices:
            matched = False

            # --- Tier 1: Exact Unique Invoice ID Match ---
            candidates = payments_by_invoice_ref.get(inv.invoice_id, [])
            available_candidates = [c for c in candidates if c.payment_id not in matched_payment_ids]

            if len(available_candidates) == 1:
                p = available_candidates[0]
                matched_results.append(
                    MatchResult(
                        invoice=inv,
                        payment=p,
                        status=MatchStatus.MATCHED,
                        method=MatchMethod.EXACT_INVOICE_ID,
                        evidence={"reason": f"Exact match on invoice_id '{inv.invoice_id}'"},
                    )
                )
                matched_payment_ids.add(p.payment_id)
                matched = True
            elif len(available_candidates) > 1:
                matched_results.append(
                    MatchResult(
                        invoice=inv,
                        payment=None,
                        status=MatchStatus.AMBIGUOUS_MATCH,
                        evidence={
                            "reason": f"Multiple Tier 1 payments referenced invoice_id '{inv.invoice_id}'",
                            "candidate_payment_ids": [c.payment_id for c in available_candidates],
                        },
                    )
                )
                matched = True

            if matched:
                continue

            # --- Tier 2: Provider Transaction ID Match ---
            if inv.payment_reference and inv.payment_reference in payments_by_id:
                p = payments_by_id[inv.payment_reference]
                if p.payment_id not in matched_payment_ids:
                    matched_results.append(
                        MatchResult(
                            invoice=inv,
                            payment=p,
                            status=MatchStatus.MATCHED,
                            method=MatchMethod.PROVIDER_TRANSACTION_ID,
                            evidence={"reason": f"Matched via provider reference '{inv.payment_reference}'"},
                        )
                    )
                    matched_payment_ids.add(p.payment_id)
                    matched = True

            if matched:
                continue

            # --- Tier 3: Composite Fallback Match ---
            composite_candidates = []
            for p in valid_payments:
                if p.payment_id in matched_payment_ids:
                    continue

                if (
                    inv.customer_reference
                    and p.customer_reference
                    and inv.customer_reference.strip().lower() == p.customer_reference.strip().lower()
                    and inv.currency == p.currency
                    and inv.total_amount == p.gross_amount
                    and abs(inv.issued_at - p.paid_at) <= self.date_window
                ):
                    composite_candidates.append(p)

            if len(composite_candidates) == 1:
                p = composite_candidates[0]
                matched_results.append(
                    MatchResult(
                        invoice=inv,
                        payment=p,
                        status=MatchStatus.MATCHED,
                        method=MatchMethod.COMPOSITE_FALLBACK,
                        evidence={
                            "reason": f"Composite match on customer '{inv.customer_reference}', amount '{inv.total_amount}', currency '{inv.currency}'"
                        },
                    )
                )
                matched_payment_ids.add(p.payment_id)
                matched = True
            elif len(composite_candidates) > 1:
                matched_results.append(
                    MatchResult(
                        invoice=inv,
                        payment=None,
                        status=MatchStatus.AMBIGUOUS_MATCH,
                        evidence={
                            "reason": f"Multiple composite candidates found for customer '{inv.customer_reference}'",
                            "candidate_payment_ids": [c.payment_id for c in composite_candidates],
                        },
                    )
                )
                matched = True

            if not matched:
                matched_results.append(
                    MatchResult(
                        invoice=inv,
                        payment=None,
                        status=MatchStatus.NO_MATCH,
                        evidence={"reason": f"No matching payment found for invoice '{inv.invoice_id}'"},
                    )
                )

        unmatched_valid = [p for p in valid_payments if p.payment_id not in matched_payment_ids]

        return matched_results, unmatched_valid, duplicate_payments
