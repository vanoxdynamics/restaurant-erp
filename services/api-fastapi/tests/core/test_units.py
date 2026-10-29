import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import jwt
import pytest
from pydantic import ValidationError

from app.core.clock import business_date, financial_year
from app.core.config import Settings, settings
from app.core.errors import ValidationFailed
from app.core.money import q
from app.core.security import (
    compute_pin_lookup,
    create_access_token,
    decode_access_token,
    get_password_hash,
    get_pin_hash,
    verify_and_update_password,
    verify_password,
    verify_pin,
)
from app.modules.billing.tax import TaxLine, allocate_discount, compute_bill

D = Decimal


# --- Security --------------------------------------------------------------------


def test_pin_and_password_hash_round_trip():
    pin_hash = get_pin_hash("1234")
    assert verify_pin("1234", pin_hash)
    assert not verify_pin("4321", pin_hash)
    pw_hash = get_password_hash("correct-horse-battery")
    assert verify_password("correct-horse-battery", pw_hash)
    assert not verify_password("wrong", pw_hash)


def test_malformed_hash_returns_false():
    assert verify_password("x", "not-a-hash") is False


def test_legacy_bcrypt_hash_verifies_and_is_upgraded():
    import bcrypt

    legacy = bcrypt.hashpw(b"old-password-123", bcrypt.gensalt()).decode()
    ok, new_hash = verify_and_update_password("old-password-123", legacy)
    assert ok and new_hash and new_hash.startswith("$argon2")


def test_token_round_trip_and_rejections():
    token = create_access_token("user-1", claims={"role": "waiter"})
    payload = decode_access_token(token)
    assert payload["sub"] == "user-1" and payload["role"] == "waiter"

    assert decode_access_token(create_access_token("u", expires_delta=timedelta(seconds=-1))) is None
    forged = jwt.encode({"sub": "u", "iat": datetime.now(timezone.utc), "exp": datetime.now(timezone.utc) + timedelta(hours=1),
                         "iss": settings.JWT_ISSUER}, "another-key-another-key-another-key", algorithm="HS256")
    assert decode_access_token(forged) is None
    no_exp = jwt.encode({"sub": "u", "iat": datetime.now(timezone.utc), "iss": settings.JWT_ISSUER},
                        settings.SECRET_KEY, algorithm="HS256")
    assert decode_access_token(no_exp) is None
    assert decode_access_token("garbage") is None


def test_pin_lookup_is_deterministic_and_branch_scoped():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert compute_pin_lookup(a, "1234") == compute_pin_lookup(a, "1234")
    assert compute_pin_lookup(a, "1234") != compute_pin_lookup(b, "1234")


def test_production_rejects_default_secrets():
    with pytest.raises(ValidationError):
        Settings(ENVIRONMENT="production", _env_file=None)
    ok = Settings(ENVIRONMENT="production", SECRET_KEY="s" * 40, PIN_PEPPER="p" * 40, _env_file=None)
    assert ok.is_production


def test_allowed_origins_comma_separated():
    s = Settings(ALLOWED_ORIGINS="http://a.test, http://b.test", _env_file=None)
    assert s.ALLOWED_ORIGINS == ["http://a.test", "http://b.test"]


# --- Clock & money -------------------------------------------------------------------


def test_business_date_uses_branch_timezone():
    # 20:00 UTC on 28 Oct is 01:30 IST on 29 Oct.
    at = datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)
    assert business_date("Asia/Kolkata", at) == date(2026, 10, 29)
    assert business_date("UTC", at) == date(2026, 10, 28)


@pytest.mark.parametrize("d,expected", [
    (date(2026, 10, 28), "2627"), (date(2027, 3, 31), "2627"), (date(2027, 4, 1), "2728"), (date(2000, 1, 1), "9900"),
])
def test_financial_year(d, expected):
    assert financial_year(d) == expected


def test_money_rounding():
    assert q(D("2.345")) == D("2.35")
    assert q(D("2.344")) == D("2.34")
    with pytest.raises(TypeError):
        q(2.345)  # type: ignore[arg-type]


# --- Tax engine ------------------------------------------------------------------------


def test_single_five_percent_line():
    b = compute_bill([TaxLine(D("250.00"), D("5.00"))], D("0"))
    assert (b.subtotal, b.cgst, b.sgst, b.grand_total) == (D("250.00"), D("6.25"), D("6.25"), D("262.50"))


def test_mixed_rates():
    b = compute_bill([TaxLine(D("100.00"), D("5.00")), TaxLine(D("200.00"), D("18.00"))], D("0"))
    # tax = 5 + 36 = 41.00
    assert b.cgst + b.sgst == D("41.00")
    assert b.cgst == D("20.50") and b.grand_total == D("341.00")


def test_odd_tax_splits_into_halves_that_add_up():
    b = compute_bill([TaxLine(D("100.10"), D("5.00"))], D("0"))
    # 5.005 -> 5.01 total; cgst 2.51 (half-up of 2.505), sgst 2.50
    assert b.cgst + b.sgst == D("5.01")
    assert b.grand_total == D("105.11")


def test_discount_applied_before_tax_and_allocated():
    lines = [TaxLine(D("100.00"), D("5.00")), TaxLine(D("200.00"), D("18.00"))]
    shares = allocate_discount(lines, D("10.00"))
    assert sum(shares) == D("10.00")
    b = compute_bill(lines, D("10.00"))
    # taxable: 96.67 @5% + 193.33 @18% = 4.8335 + 34.7994 = 39.63
    assert b.taxable_value == D("290.00")
    assert b.cgst + b.sgst == D("39.63")
    assert b.grand_total == D("329.63")


def test_discount_remainder_goes_to_largest_line():
    lines = [TaxLine(D("10.00"), D("5")), TaxLine(D("10.00"), D("5")), TaxLine(D("10.00"), D("5"))]
    shares = allocate_discount(lines, D("10.00"))
    assert sum(shares) == D("10.00")
    assert sorted(shares) == [D("3.33"), D("3.33"), D("3.34")]


def test_full_discount():
    b = compute_bill([TaxLine(D("100.00"), D("5.00"))], D("100.00"))
    assert b.grand_total == D("0.00") and b.cgst == D("0.00")


def test_invalid_inputs():
    with pytest.raises(ValidationFailed):
        compute_bill([TaxLine(D("100.00"), D("5"))], D("100.01"))
    with pytest.raises(ValidationFailed):
        compute_bill([], D("0"))
    with pytest.raises(TypeError):
        compute_bill([TaxLine(100.0, D("5"))], D("0"))  # type: ignore[arg-type]
