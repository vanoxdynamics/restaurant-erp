"""
Shop tools for the retail app (cafés, grocery, general retail): finding an item by what a barcode scanner read, and loading a
catalogue from a spreadsheet. Items themselves are the shared catalog (app.modules.catalog); orders, billing and payments are
shared with restaurants.
"""

import csv
import io
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ItemUnit, Station, Topic
from app.core.errors import NotFound, ValidationFailed
from app.core.events import commit, queue_event
from app.models.catalog import Category, MenuItem
from app.models.floor import Branch
from app.modules.catalog.service import _flush_item_codes, get_item
from app.schemas.retail import ItemImportResult


async def lookup_item(db: AsyncSession, org_id: uuid.UUID, code: str) -> MenuItem:
    """The active item whose barcode or SKU is exactly `code` (what a scanner typed)."""
    code = code.strip()
    item_id = await db.scalar(
        select(MenuItem.id).where(
            MenuItem.organization_id == org_id, MenuItem.is_active.is_(True),
            or_(MenuItem.barcode == code, MenuItem.sku == code),
        ).order_by((MenuItem.barcode == code).desc()).limit(1)  # a barcode match wins over a SKU match
    )
    if item_id is None:
        raise NotFound("No item has this barcode", code="barcode_not_found", details={"code": code})
    return await get_item(db, org_id, item_id)


IMPORT_MAX_ROWS = 10_000
IMPORT_COLUMNS = ("name", "category", "price", "gst_rate", "hsn", "unit", "barcode", "sku", "sold_by_weight")
_COLUMN_ALIASES = {
    "item": "name", "item_name": "name", "product": "name", "mrp": "price", "rate": "price", "selling_price": "price",
    "gst": "gst_rate", "tax": "gst_rate", "tax_rate": "gst_rate", "hsn_code": "hsn", "hsn_sac": "hsn", "ean": "barcode",
    "weighed": "sold_by_weight", "loose": "sold_by_weight",
}
_UNIT_ALIASES = {"": "pcs", "pc": "pcs", "piece": "pcs", "pieces": "pcs", "nos": "pcs", "no": "pcs", "each": "pcs",
                 "kgs": "kg", "kilo": "kg", "gm": "g", "gms": "g", "gram": "g", "grams": "g", "ltr": "l", "litre": "l",
                 "liter": "l", "lt": "l", "mls": "ml"}
_TRUE = {"1", "y", "yes", "true", "t"}
_FALSE = {"", "0", "n", "no", "false", "f"}
_GST_RATES = {Decimal(r) for r in ("0", "0.25", "3", "5", "12", "18", "28")}
CENTS = Decimal("0.01")


def _header_key(raw: str) -> str:
    key = raw.strip().lower().replace(" ", "_").replace("-", "_").replace("%", "").strip("_")
    return _COLUMN_ALIASES.get(key, key)


def _printable_code(value: str) -> bool:
    return len(value) <= 64 and all(0x21 <= ord(c) <= 0x7E for c in value)


def _parse_row(raw: Dict[str, str]) -> tuple:
    """One CSV row -> (clean values, list of problems)."""
    errors: List[str] = []

    def get(key: str) -> str:
        return (raw.get(key) or "").strip()

    row: Dict[str, Any] = {"name": get("name"), "category": get("category")}
    if not row["name"]:
        errors.append("name is missing")
    elif len(row["name"]) > 150:
        errors.append("name is longer than 150 characters")
    if not row["category"]:
        errors.append("category is missing")
    elif len(row["category"]) > 100:
        errors.append("category is longer than 100 characters")

    try:
        price = Decimal(get("price").replace(",", ""))
        if not price.is_finite() or price < 0 or price >= Decimal("100000000") or price != price.quantize(CENTS):
            raise InvalidOperation
        row["price"] = price
    except InvalidOperation:
        errors.append(f"price '{get('price')}' is not an amount like 49.50")

    gst = get("gst_rate") or "5"
    try:
        rate = Decimal(gst)
        if rate not in _GST_RATES:
            raise InvalidOperation
        row["tax_rate"] = rate.quantize(CENTS)
    except InvalidOperation:
        errors.append(f"gst_rate '{gst}' is not a GST rate (0, 0.25, 3, 5, 12, 18 or 28)")

    hsn = get("hsn")
    if hsn and not (hsn.isdigit() and 4 <= len(hsn) <= 8):
        errors.append(f"hsn '{hsn}' must be 4 to 8 digits")
    row["hsn_code"] = hsn or None

    unit = _UNIT_ALIASES.get(get("unit").lower(), get("unit").lower())
    if unit not in {u.value for u in ItemUnit}:
        errors.append(f"unit '{get('unit')}' must be one of pcs, kg, g, l, ml")
    row["unit"] = unit

    for key in ("barcode", "sku"):
        value = get(key)
        if value and not _printable_code(value):
            errors.append(f"{key} '{value}' must be up to 64 characters without spaces")
        row[key] = value or None

    weighed = get("sold_by_weight").lower()
    if weighed in _TRUE:
        row["sold_by_weight"] = True
    elif weighed == "":
        row["sold_by_weight"] = unit in ("kg", "g", "l", "ml")  # blank: loose when priced per kg/g/l/ml
    elif weighed in _FALSE:
        row["sold_by_weight"] = False
    else:
        errors.append(f"sold_by_weight '{weighed}' must be yes or no")
    return row, errors


async def import_items(db: AsyncSession, org_id: uuid.UUID, text: str, dry_run: bool) -> ItemImportResult:
    """
    Create or update items from CSV. Every row is checked first; rows with errors are reported and skipped, the rest are
    imported together in one transaction.
    """
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))  # Excel writes a byte-order mark
    header = next(reader, None)
    if not header or not any(h.strip() for h in header):
        raise ValidationFailed("The file is empty", code="empty_import")
    headers = [_header_key(h) for h in header]
    missing = [c for c in ("name", "category", "price") if c not in headers]
    if missing:
        raise ValidationFailed(f"Missing column(s): {', '.join(missing)}", code="import_columns_missing",
                               details={"missing": missing, "expected": list(IMPORT_COLUMNS)})

    rows: List[tuple] = []
    errors: List[Dict[str, Any]] = []
    seen: Dict[tuple, int] = {}
    count = 0
    for line, cells in enumerate(reader, start=2):  # line 1 is the header
        if not any(c.strip() for c in cells):
            continue  # blank line
        count += 1
        if count > IMPORT_MAX_ROWS:
            raise ValidationFailed(f"At most {IMPORT_MAX_ROWS} rows per file; split the file", code="import_too_large")
        raw = {headers[i]: cells[i] for i in range(min(len(headers), len(cells)))}
        row, row_errors = _parse_row(raw)
        for key in ("barcode", "sku"):
            if row.get(key):
                first = seen.setdefault((key, row[key]), line)
                if first != line:
                    row_errors.append(f"{key} {row[key]} is also on line {first}")
        if row_errors:
            errors.append({"row": line, "errors": row_errors,
                           "data": {k: raw[k].strip() for k in IMPORT_COLUMNS if (raw.get(k) or "").strip()}})
        else:
            rows.append((line, row))

    # The existing catalog, indexed the ways a row can match it.
    items = (await db.scalars(select(MenuItem).where(MenuItem.organization_id == org_id))).all()
    categories = {c.name.strip().lower(): c for c in
                  (await db.scalars(select(Category).where(Category.organization_id == org_id))).all()}
    by_sku = {i.sku: i for i in items if i.sku}
    by_barcode = {i.barcode: i for i in items if i.barcode}
    by_name = {(i.category_id, i.name.strip().lower()): i for i in items}

    created = updated = categories_created = 0
    touched: set = set()
    for line, row in rows:
        category = categories.get(row["category"].lower())
        if category is None:
            category = Category(id=uuid.uuid4(), organization_id=org_id, name=row["category"], is_active=True, sort_order=0)
            if not dry_run:
                db.add(category)
            categories[row["category"].lower()] = category
            categories_created += 1
        elif not category.is_active and not dry_run:
            category.is_active = True

        item = (by_sku.get(row["sku"]) if row["sku"] else None) \
            or (by_barcode.get(row["barcode"]) if row["barcode"] else None) \
            or by_name.get((category.id, row["name"].lower()))
        # A code must not move onto a different item than the one that already holds it.
        clash = None
        for key, index in (("barcode", by_barcode), ("sku", by_sku)):
            holder = index.get(row[key]) if row[key] else None
            if holder is not None and holder is not item:
                clash = f"{key} {row[key]} already belongs to '{holder.name}'"
        if clash is None and item is not None and id(item) in touched:
            clash = "an earlier line already updates this item"
        if clash:
            errors.append({"row": line, "errors": [clash],
                           "data": {k: str(row[k]) for k in ("name", "category", "barcode", "sku") if row.get(k)}})
            continue

        values = {"name": row["name"], "category_id": category.id, "base_price": row["price"], "tax_rate": row["tax_rate"],
                  "hsn_code": row["hsn_code"], "unit": row["unit"], "barcode": row["barcode"], "sku": row["sku"],
                  "sold_by_weight": row["sold_by_weight"], "is_active": True}
        if item is None:
            item = MenuItem(id=uuid.uuid4(), organization_id=org_id, station=Station.KITCHEN.value, is_available_86=True,
                            sort_order=0, **values)
            if not dry_run:
                db.add(item)
            created += 1
        else:
            if not dry_run:
                for key, value in values.items():
                    setattr(item, key, value)
            updated += 1
        touched.add(id(item))
        for key, index in (("sku", by_sku), ("barcode", by_barcode)):
            if row[key]:
                index[row[key]] = item
        by_name[(category.id, row["name"].lower())] = item

    errors.sort(key=lambda e: e["row"])
    if dry_run:
        await db.rollback()
    else:
        await _flush_item_codes(db)
        branch_ids = (await db.scalars(
            select(Branch.id).where(Branch.is_active.is_(True), Branch.organization_id == org_id))).all()
        for branch_id in branch_ids:
            queue_event(db, branch_id, Topic.CATALOG, "catalog.imported", org_id, {"created": created, "updated": updated})
        await commit(db)
    return ItemImportResult(created=created, updated=updated, categories_created=categories_created, errors=errors,
                            dry_run=dry_run)
