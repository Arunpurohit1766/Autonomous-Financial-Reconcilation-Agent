"""Robust CSV ingestion parser with header whitespace normalization and row-level error tracking."""

import csv
import io
from typing import Tuple, List, Dict, Any, Type, TypeVar
from pydantic import BaseModel, ValidationError

from app.schemas.invoice import InvoiceRecordCreate
from app.schemas.payment import PaymentRecordCreate

T = TypeVar("T", bound=BaseModel)


class RowValidationError(BaseModel):
    """Details for a single invalid or malformed row in a CSV file."""
    row_number: int
    raw_data: Dict[str, Any]
    errors: List[Dict[str, Any]]


def parse_csv_stream(
    content: str,
    schema_cls: Type[T]
) -> Tuple[List[T], List[RowValidationError]]:
    """
    Parse CSV text content against a Pydantic schema.
    Normalizes header whitespace and catches malformed row schemas.
    """
    valid_records: List[T] = []
    errors: List[RowValidationError] = []

    if not content or not content.strip():
        return valid_records, errors

    f = io.StringIO(content.strip())
    raw_reader = csv.reader(f)

    try:
        raw_headers = next(raw_reader)
    except StopIteration:
        return valid_records, errors

    # 1. Normalize header whitespace
    clean_headers = [h.strip() for h in raw_headers if h is not None]
    if not clean_headers:
        return valid_records, errors

    expected_len = len(clean_headers)

    for row_idx, raw_values in enumerate(raw_reader, start=2):
        if not raw_values or not any(v.strip() for v in raw_values):
            continue  # skip completely blank rows

        # Handle column count mismatch
        if len(raw_values) != expected_len:
            errors.append(
                RowValidationError(
                    row_number=row_idx,
                    raw_data={"raw_values": raw_values},
                    errors=[{
                        "type": "column_count_mismatch",
                        "msg": f"Expected {expected_len} columns, got {len(raw_values)}",
                    }],
                )
            )
            continue

        cleaned_row: Dict[str, Any] = {}
        for header, val in zip(clean_headers, raw_values):
            stripped_val = val.strip() if isinstance(val, str) else val
            cleaned_row[header] = stripped_val if stripped_val != "" else None

        cleaned_row["source_row_number"] = row_idx

        try:
            record = schema_cls.model_validate(cleaned_row)
            valid_records.append(record)
        except ValidationError as e:
            errors.append(
                RowValidationError(
                    row_number=row_idx,
                    raw_data=cleaned_row,
                    errors=e.errors(),
                )
            )

    return valid_records, errors


def parse_invoices_csv(content: str) -> Tuple[List[InvoiceRecordCreate], List[RowValidationError]]:
    return parse_csv_stream(content, InvoiceRecordCreate)


def parse_payments_csv(content: str) -> Tuple[List[PaymentRecordCreate], List[RowValidationError]]:
    return parse_csv_stream(content, PaymentRecordCreate)
