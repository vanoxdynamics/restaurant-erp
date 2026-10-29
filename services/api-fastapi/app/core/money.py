from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def q(x: Decimal) -> Decimal:
    """Quantize a monetary amount to paise using half-up rounding."""
    if not isinstance(x, Decimal):
        raise TypeError(f"Monetary values must be Decimal, got {type(x).__name__}")
    return x.quantize(CENT, rounding=ROUND_HALF_UP)
