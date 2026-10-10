"""Comprehensive Phase 1 test suite covering 3-tier matching, ambiguity, duplicates, tolerances, and reporting."""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from app.domain.enums import MatchStatus, MatchMethod, ExceptionType
from app.schemas.invoice import InvoiceRecordCreate
from app.schemas.payment import PaymentRecordCreate
from app.ingestion.csv_parser import parse_invoices_csv, parse_payments_csv
from app.reconciliation.matcher import DeterministicMatcher, MatchResult
from app.reconciliation.calculator import ReconciliationCalculator
from app.reporting.json_report import generate_json_report


# --- 1. CSV Ingestion & Header Normalization Tests ---

def test_csv_parser_header_whitespace_normalization():
    csv_data = """  invoice_id  , customer_reference , issued_at , currency , subtotal , tax_amount , discount_amount , total_amount , payment_reference
INV-SPACE-1, ACME CORP ,2024-10-01T10:00:00Z,USD,100.00,0.00,0.00,100.00,REF-1
"""
    records, errors = parse_invoices_csv(csv_data)
    assert len(errors) == 0
    assert len(records) == 1
    assert records[0].invoice_id == "INV-SPACE-1"
    assert records[0].customer_reference == "ACME CORP"


def test_csv_parser_column_count_mismatch():
    csv_data = """invoice_id,customer_reference,issued_at,currency,subtotal,tax_amount,discount_amount,total_amount,payment_reference
INV-BAD,ACME,2024-10-01T10:00:00Z,USD,100.00
"""
    records, errors = parse_invoices_csv(csv_data)
    assert len(records) == 0
    assert len(errors) == 1
    assert errors[0].errors[0]["type"] == "column_count_mismatch"


# --- 2. Ambiguity & Duplicate Detection Tests ---

def test_tier1_multiple_candidates_produce_ambiguity():
    inv = InvoiceRecordCreate(
        invoice_id="INV-AMBIG-1",
        issued_at=datetime.now(timezone.utc),
        currency="USD",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        source_row_number=1,
    )
    p1 = PaymentRecordCreate(
        payment_id="PAY-A",
        invoice_reference="INV-AMBIG-1",
        paid_at=datetime.now(timezone.utc),
        currency="USD",
        gross_amount=Decimal("100.00"),
        source_row_number=1,
    )
    p2 = PaymentRecordCreate(
        payment_id="PAY-B",
        invoice_reference="INV-AMBIG-1",
        paid_at=datetime.now(timezone.utc),
        currency="USD",
        gross_amount=Decimal("100.00"),
        source_row_number=2,
    )

    matcher = DeterministicMatcher()
    results, unmatched_valid, duplicate_payments = matcher.match([inv], [p1, p2])

    assert len(results) == 1
    assert results[0].status == MatchStatus.AMBIGUOUS_MATCH
    assert results[0].payment is None

    calc = ReconciliationCalculator()
    items = calc.reconcile(results, unmatched_valid, duplicate_payments)

    assert len(items) == 3  # 1 ambiguity item + 2 unconsumed payment items
    assert items[0].exception_type == ExceptionType.AMBIGUOUS_MATCH
    assert items[0].rule_id == "RULE-007-AMBIGUOUS-MATCH"


def test_duplicate_payment_ids_reported_as_data_quality_error_not_orphan():
    """Verify duplicate payments are classified as DATA_QUALITY_ERROR, never INVOICE_NOT_FOUND."""
    inv = InvoiceRecordCreate(
        invoice_id="INV-DUP-1",
        issued_at=datetime.now(timezone.utc),
        currency="USD",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        source_row_number=1,
    )
    p1 = PaymentRecordCreate(
        payment_id="PAY-DUP",
        invoice_reference="INV-DUP-1",
        paid_at=datetime.now(timezone.utc),
        currency="USD",
        gross_amount=Decimal("100.00"),
        source_row_number=1,
    )
    p2 = PaymentRecordCreate(
        payment_id="PAY-DUP",  # Duplicate payment_id
        invoice_reference="INV-DUP-1",
        paid_at=datetime.now(timezone.utc),
        currency="USD",
        gross_amount=Decimal("100.00"),
        source_row_number=2,
    )

    matcher = DeterministicMatcher()
    results, unmatched_valid, duplicate_payments = matcher.match([inv], [p1, p2])

    assert len(duplicate_payments) == 2
    assert len(unmatched_valid) == 0

    calc = ReconciliationCalculator()
    items = calc.reconcile(results, unmatched_valid, duplicate_payments)

    # 1 invoice not found + 2 duplicate payment errors
    dup_items = [it for it in items if it.rule_id == "RULE-008-DUPLICATE-PAYMENT-ID"]
    assert len(dup_items) == 2
    for it in dup_items:
        assert it.exception_type == ExceptionType.DATA_QUALITY_ERROR
        assert it.exception_type != ExceptionType.INVOICE_NOT_FOUND


# --- 3. Three-Tier Matching Precedence Tests ---

def test_tier2_provider_id_match_and_consumption():
    inv = InvoiceRecordCreate(
        invoice_id="INV-T2",
        payment_reference="STRIPE-CHG-100",
        issued_at=datetime.now(timezone.utc),
        currency="USD",
        subtotal=Decimal("200.00"),
        total_amount=Decimal("200.00"),
        source_row_number=1,
    )
    pmt = PaymentRecordCreate(
        payment_id="STRIPE-CHG-100",
        paid_at=datetime.now(timezone.utc),
        currency="USD",
        gross_amount=Decimal("200.00"),
        source_row_number=1,
    )

    matcher = DeterministicMatcher()
    results, unmatched_valid, duplicates = matcher.match([inv], [pmt])

    assert len(results) == 1
    assert results[0].status == MatchStatus.MATCHED
    assert results[0].method == MatchMethod.PROVIDER_TRANSACTION_ID
    assert len(unmatched_valid) == 0
    assert len(duplicates) == 0


def test_tier3_composite_match_date_window():
    now = datetime.now(timezone.utc)
    inv = InvoiceRecordCreate(
        invoice_id="INV-T3",
        customer_reference="globex corp",
        issued_at=now,
        currency="USD",
        subtotal=Decimal("300.00"),
        total_amount=Decimal("300.00"),
        source_row_number=1,
    )
    pmt_in_window = PaymentRecordCreate(
        payment_id="PAY-IN-WIN",
        customer_reference="GLOBEX CORP",
        paid_at=now + timedelta(days=2),
        currency="USD",
        gross_amount=Decimal("300.00"),
        source_row_number=1,
    )

    matcher = DeterministicMatcher(date_window_days=3)
    results, unmatched_valid, duplicates = matcher.match([inv], [pmt_in_window])

    assert len(results) == 1
    assert results[0].status == MatchStatus.MATCHED
    assert results[0].method == MatchMethod.COMPOSITE_FALLBACK
    assert len(unmatched_valid) == 0
    assert len(duplicates) == 0


def test_tier3_outside_date_window_fails():
    now = datetime.now(timezone.utc)
    inv = InvoiceRecordCreate(
        invoice_id="INV-T3-OUT",
        customer_reference="Globex",
        issued_at=now,
        currency="USD",
        subtotal=Decimal("300.00"),
        total_amount=Decimal("300.00"),
        source_row_number=1,
    )
    pmt_out_window = PaymentRecordCreate(
        payment_id="PAY-OUT-WIN",
        customer_reference="Globex",
        paid_at=now + timedelta(days=10),
        currency="USD",
        gross_amount=Decimal("300.00"),
        source_row_number=1,
    )

    matcher = DeterministicMatcher(date_window_days=3)
    results, unmatched_valid, duplicates = matcher.match([inv], [pmt_out_window])

    assert results[0].status == MatchStatus.NO_MATCH
    assert len(unmatched_valid) == 1
    assert len(duplicates) == 0


# --- 4. Financial Calculations & Tolerance Tests ---

def test_tolerance_exact_and_boundary():
    now = datetime.now(timezone.utc)
    inv = InvoiceRecordCreate(
        invoice_id="INV-TOL",
        issued_at=now,
        currency="USD",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        source_row_number=1,
    )
    calc = ReconciliationCalculator(tolerance=Decimal("0.01"))

    # Case A: Diff exactly 0.01 (within tolerance)
    p_eq = PaymentRecordCreate(
        payment_id="P-EQ",
        invoice_reference="INV-TOL",
        paid_at=now,
        currency="USD",
        gross_amount=Decimal("100.01"),
        source_row_number=1,
    )
    match_eq = MatchResult(invoice=inv, payment=p_eq, status=MatchStatus.MATCHED)
    assert calc.evaluate_pair(match_eq).is_reconciled is True

    # Case B: Diff 0.02 (exceeds tolerance -> AMOUNT_MISMATCH)
    p_gt = PaymentRecordCreate(
        payment_id="P-GT",
        invoice_reference="INV-TOL",
        paid_at=now,
        currency="USD",
        gross_amount=Decimal("100.02"),
        source_row_number=1,
    )
    match_gt = MatchResult(invoice=inv, payment=p_gt, status=MatchStatus.MATCHED)
    res_gt = calc.evaluate_pair(match_gt)
    assert res_gt.is_reconciled is False
    assert res_gt.exception_type == ExceptionType.AMOUNT_MISMATCH
    assert res_gt.difference_amount == Decimal("0.02")


# --- 5. Clean & Mismatch Fixture Regression Tests ---

def test_fixtures_regression():
    with open("tests/fixtures/clean_invoices.csv") as f:
        clean_invs, _ = parse_invoices_csv(f.read())
    with open("tests/fixtures/clean_payments.csv") as f:
        clean_pmts, _ = parse_payments_csv(f.read())

    matcher = DeterministicMatcher()
    clean_res, clean_unm, clean_dups = matcher.match(clean_invs, clean_pmts)
    calc = ReconciliationCalculator()
    clean_items = calc.reconcile(clean_res, clean_unm, clean_dups)
    clean_report = generate_json_report("job-clean", clean_items)

    assert clean_report.summary.reconciliation_rate_percent == 100.0
    assert clean_report.summary.total_exceptions == 0
    assert len(clean_dups) == 0
