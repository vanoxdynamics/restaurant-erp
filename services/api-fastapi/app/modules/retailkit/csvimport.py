"""
Reading a catalogue spreadsheet (CSV) for the shop modules.

The same approach as the item import in `app.modules.retail.service`: headers are normalised and matched through aliases,
every row is checked and its problems collected with the line number, and a file has a row limit. What a row means (and how it
is saved) belongs to each module; this file only reads, cleans and reports.
"""

import csv
import io
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.core.errors import ValidationFailed

MAX_ROWS = 10_000
CENTS = Decimal("0.01")
TRUE = {"1", "y", "yes", "true", "t"}
FALSE = {"0", "n", "no", "false", "f"}


def header_key(raw: str, aliases: Mapping[str, str]) -> str:
    key = raw.strip().lower().replace(" ", "_").replace("-", "_").replace("%", "").strip("_")
    return aliases.get(key, key)


def read_rows(
    text: str, *, aliases: Mapping[str, str], required: Sequence[str], expected: Sequence[str], max_rows: int = MAX_ROWS,
) -> List[Tuple[int, Dict[str, str]]]:
    """
    (line number, {column: stripped cell}) for every non-blank row. Raises for an empty file, missing required columns or too
    many rows; problems inside rows are for the caller to collect.
    """
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))  # Excel writes a byte-order mark
    header = next(reader, None)
    if not header or not any(h.strip() for h in header):
        raise ValidationFailed("The file is empty", code="empty_import")
    headers = [header_key(h, aliases) for h in header]
    missing = [c for c in required if c not in headers]
    if missing:
        raise ValidationFailed(f"Missing column(s): {', '.join(missing)}", code="import_columns_missing",
                               details={"missing": missing, "expected": list(expected)})
    out: List[Tuple[int, Dict[str, str]]] = []
    for line, cells in enumerate(reader, start=2):  # line 1 is the header
        if not any(c.strip() for c in cells):
            continue
        if len(out) >= max_rows:
            raise ValidationFailed(f"At most {max_rows} rows per file; split the file", code="import_too_large")
        out.append((line, {headers[i]: cells[i].strip() for i in range(min(len(headers), len(cells)))}))
    return out


class RowErrors:
    """Problems found in one row, plus a short echo of the row for the person fixing the file."""

    def __init__(self) -> None:
        self.errors: List[str] = []

    def add(self, message: str) -> None:
        self.errors.append(message)

    def __bool__(self) -> bool:
        return bool(self.errors)


def error_row(line: int, errors: Iterable[str], raw: Mapping[str, Any], columns: Sequence[str]) -> Dict[str, Any]:
    return {"row": line, "errors": list(errors), "data": {k: str(raw[k]) for k in columns if str(raw.get(k) or "").strip()}}


def text(raw: Mapping[str, str], key: str, errors: RowErrors, *, max_len: int, required: bool = False) -> Optional[str]:
    value = (raw.get(key) or "").strip()
    if not value:
        if required:
            errors.add(f"{key} is missing")
        return None
    if len(value) > max_len:
        errors.add(f"{key} is longer than {max_len} characters")
    return value


def money(raw: Mapping[str, str], key: str, errors: RowErrors, *, required: bool = False) -> Optional[Decimal]:
    value = (raw.get(key) or "").strip().replace(",", "")
    if not value:
        if required:
            errors.add(f"{key} is missing")
        return None
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount >= Decimal("100000000") or amount != amount.quantize(CENTS):
            raise InvalidOperation
        return amount
    except InvalidOperation:
        errors.add(f"{key} '{value}' is not an amount like 49.50")
        return None


def number(raw: Mapping[str, str], key: str, errors: RowErrors, *, places: int, positive: bool = False) -> Optional[Decimal]:
    value = (raw.get(key) or "").strip().replace(",", "")
    if not value:
        return None
    try:
        amount = Decimal(value)
        step = Decimal(1).scaleb(-places)
        if not amount.is_finite() or amount < 0 or (positive and amount == 0) or amount >= Decimal("1000000000") \
                or amount != amount.quantize(step):
            raise InvalidOperation
        return amount
    except InvalidOperation:
        kind = "a positive number" if positive else "a number"
        errors.add(f"{key} '{value}' is not {kind} with at most {places} decimals")
        return None


def flag(raw: Mapping[str, str], key: str, errors: RowErrors) -> Optional[bool]:
    value = (raw.get(key) or "").strip().lower()
    if value == "":
        return None
    if value in TRUE:
        return True
    if value in FALSE:
        return False
    errors.add(f"{key} '{value}' must be yes or no")
    return None


def printable_code(value: str) -> bool:
    return len(value) <= 64 and all(0x21 <= ord(c) <= 0x7E for c in value)


def code(raw: Mapping[str, str], key: str, errors: RowErrors) -> Optional[str]:
    value = (raw.get(key) or "").strip()
    if value and not printable_code(value):
        errors.add(f"{key} '{value}' must be up to 64 characters without spaces")
    return value or None


def hsn(raw: Mapping[str, str], key: str, errors: RowErrors) -> Optional[str]:
    value = (raw.get(key) or "").strip()
    if value and not (value.isdigit() and 4 <= len(value) <= 8):
        errors.add(f"{key} '{value}' must be 4 to 8 digits")
    return value or None
