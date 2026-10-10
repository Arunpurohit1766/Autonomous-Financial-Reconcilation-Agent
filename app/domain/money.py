"""Domain money utilities enforcing Decimal arithmetic and explicit rounding."""

from decimal import Decimal, ROUND_HALF_UP
from typing import Union

MONEY_QUANTUM = Decimal("0.01")


def to_money(val: Union[str, int, float, Decimal]) -> Decimal:
    """
    Convert an incoming scalar value to a penny-quantized Decimal
    using ROUND_HALF_UP rounding.
    """
    if isinstance(val, float):
        val = str(val)
    d = Decimal(str(val))
    return d.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def validate_invoice_totals(
    subtotal: Decimal,
    tax_amount: Decimal,
    discount_amount: Decimal,
    total_amount: Decimal,
) -> bool:
    """
    Verify arithmetic consistency:
    total_amount must equal (subtotal + tax_amount - discount_amount)
    """
    sub = to_money(subtotal)
    tax = to_money(tax_amount)
    disc = to_money(discount_amount)
    tot = to_money(total_amount)
    expected = (sub + tax - disc).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    return tot == expected
