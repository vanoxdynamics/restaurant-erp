"""
Loading a hardware shop's catalogue from a spreadsheet: one row per product, optionally with one alternate unit. Plan:
docs/retail-modules/02-hardware.md section 5.

A row matches an existing product by SKU, then barcode, then (category, name, size). Every row is checked first; bad rows are
reported with their line number and skipped, the good ones are saved together in one transaction. `dry_run` saves nothing.
On an existing product a blank `unit`, `gst_rate`, `sku` or `barcode` keeps what the product has; blank prices clear.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.enums import HardwareUnit
from app.core.events import commit
from app.models.hardware import HardwareCategory, HardwareProduct, HardwareProductUnit
from app.modules.hardware.catalog import announce, default_fraction, flush_or_conflict, load_settings, lock_codes
from app.modules.retailkit import csvimport as kit
from app.schemas.hardware import HardwareImportResult

COLUMNS = ("name", "category", "brand", "size", "price", "wholesale_price", "min_price", "mrp", "gst_rate", "hsn", "unit",
           "allow_fraction", "barcode", "sku", "alt_unit", "alt_factor", "alt_price", "alt_barcode")
REQUIRED = ("name", "category", "price")
ALIASES = {
    "product": "name", "item": "name", "item_name": "name", "product_name": "name",
    "size_spec": "size", "spec": "size", "selling_price": "price", "sale_price": "price", "rate": "price",
    "retail_price": "price", "wholesale": "wholesale_price", "dealer_price": "wholesale_price", "minimum_price": "min_price",
    "min": "min_price", "ean": "barcode", "gst": "gst_rate", "tax": "gst_rate", "tax_rate": "gst_rate", "hsn_code": "hsn",
    "hsn_sac": "hsn", "uom": "unit", "base_unit": "unit", "loose": "allow_fraction", "fraction": "allow_fraction",
    "alternate_unit": "alt_unit", "pack_unit": "alt_unit", "alt_qty": "alt_factor", "pack_size": "alt_factor",
    "alt_rate": "alt_price", "pack_price": "alt_price", "alt_ean": "alt_barcode", "pack_barcode": "alt_barcode",
}
UNIT_ALIASES = {
    "pc": "pcs", "piece": "pcs", "pieces": "pcs", "nos": "pcs", "no": "pcs", "each": "pcs", "unit": "pcs", "units": "pcs",
    "boxes": "box", "packet": "pkt", "packets": "pkt", "pack": "pkt", "doz": "dozen", "dz": "dozen", "sets": "set",
    "pairs": "pair", "pr": "pair", "bags": "bag", "bdl": "bundle", "bundles": "bundle", "rolls": "roll", "coils": "coil",
    "kgs": "kg", "kilo": "kg", "kilogram": "kg", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    "mtr": "m", "mtrs": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m", "rmt": "m",
    "feet": "ft", "foot": "ft", "rft": "ft", "sq_ft": "sqft", "sq.ft": "sqft", "sqfeet": "sqft",
    "ltr": "l", "ltrs": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l", "lt": "l", "mls": "ml",
}
PRICE_PLACES = 4  # per base unit


@dataclass
class Row:
    line: int
    name: str
    category: str
    price: Decimal
    brand: Optional[str] = None
    size: Optional[str] = None
    wholesale_price: Optional[Decimal] = None
    min_price: Optional[Decimal] = None
    mrp: Optional[Decimal] = None
    gst_rate: Optional[Decimal] = None
    hsn: Optional[str] = None
    unit: Optional[str] = None
    allow_fraction: Optional[bool] = None
    barcode: Optional[str] = None
    sku: Optional[str] = None
    alt_unit: Optional[str] = None
    alt_factor: Optional[Decimal] = None
    alt_price: Optional[Decimal] = None
    alt_barcode: Optional[str] = None
    raw: Dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.category.lower(), self.name.lower(), (self.size or "").lower())


def _unit(raw: Dict[str, str], key: str, errors: kit.RowErrors) -> Optional[str]:
    value = (raw.get(key) or "").strip().lower().replace(" ", "_")
    if not value:
        return None
    value = UNIT_ALIASES.get(value, value)
    if value not in {u.value for u in HardwareUnit}:
        errors.add(f"{key} '{raw.get(key)}' is not a unit this shop can use")
        return None
    return value


def _price(raw: Dict[str, str], key: str, errors: kit.RowErrors, *, required: bool = False) -> Optional[Decimal]:
    if required and not (raw.get(key) or "").strip():
        errors.add(f"{key} is missing")
        return None
    value = kit.number(raw, key, errors, places=PRICE_PLACES)
    if value is not None and value >= Decimal("100000000"):
        errors.add(f"{key} '{raw.get(key)}' is too large")
        return None
    return value


def parse(line: int, raw: Dict[str, str]) -> Tuple[Optional[Row], List[str]]:
    errors = kit.RowErrors()
    name = kit.text(raw, "name", errors, max_len=150, required=True)
    category = kit.text(raw, "category", errors, max_len=100, required=True)
    brand = kit.text(raw, "brand", errors, max_len=100)
    size = kit.text(raw, "size", errors, max_len=60)
    price = _price(raw, "price", errors, required=True)
    wholesale = _price(raw, "wholesale_price", errors)
    minimum = _price(raw, "min_price", errors)
    mrp = _price(raw, "mrp", errors)
    gst = kit.number(raw, "gst_rate", errors, places=2)
    if gst is not None and gst > 40:
        errors.add(f"gst_rate '{raw.get('gst_rate')}' must be between 0 and 40")
    hsn = kit.hsn(raw, "hsn", errors)
    unit = _unit(raw, "unit", errors)
    fraction = kit.flag(raw, "allow_fraction", errors)
    barcode = kit.code(raw, "barcode", errors)
    sku = kit.code(raw, "sku", errors)
    alt_unit = _unit(raw, "alt_unit", errors)
    alt_factor = kit.number(raw, "alt_factor", errors, places=4, positive=True)
    alt_price = kit.money(raw, "alt_price", errors)
    alt_barcode = kit.code(raw, "alt_barcode", errors)
    given = {k: bool((raw.get(k) or "").strip()) for k in ("alt_unit", "alt_factor", "alt_price", "alt_barcode")}
    if not given["alt_unit"] and (given["alt_factor"] or given["alt_price"] or given["alt_barcode"]):
        errors.add("alt_factor, alt_price and alt_barcode need an alt_unit")
    if given["alt_unit"] and not given["alt_factor"]:
        errors.add("alt_unit needs alt_factor: how many base units one of it holds")
    if barcode and barcode == alt_barcode:
        errors.add("barcode and alt_barcode are the same")
    if errors:
        return None, errors.errors
    assert name and category and price is not None
    return Row(line=line, name=name, category=category, price=price, brand=brand, size=size, wholesale_price=wholesale,
               min_price=minimum, mrp=mrp, gst_rate=gst, hsn=hsn, unit=unit, allow_fraction=fraction, barcode=barcode,
               sku=sku, alt_unit=alt_unit, alt_factor=alt_factor, alt_price=alt_price, alt_barcode=alt_barcode, raw=raw), []


def _check_file(raw_rows: List[Tuple[int, Dict[str, str]]]) -> Tuple[List[Row], List[Dict[str, Any]]]:
    """Each row on its own, then clashes between rows of the file (codes, and the same product twice)."""
    errors: List[Dict[str, Any]] = []
    rows: List[Row] = []
    seen: Dict[Tuple[str, str], int] = {}
    for line, raw in raw_rows:
        row, problems = parse(line, raw)
        if row is not None:
            for kind, value in (("barcode", row.barcode), ("barcode", row.alt_barcode), ("sku", row.sku),
                                ("product", "|".join(row.key))):
                if value:
                    first = seen.setdefault((kind, value), line)
                    if first != line:
                        problems.append(f"{'this product' if kind == 'product' else f'{kind} {value}'} is also on line {first}")
        if problems:
            errors.append(kit.error_row(line, problems, raw, COLUMNS))
        elif row is not None:
            rows.append(row)
    return rows, errors


async def import_catalogue(db: AsyncSession, user: CurrentUser, text: str, dry_run: bool) -> HardwareImportResult:
    org_id = user.organization_id
    rows, errors = _check_file(kit.read_rows(text, aliases=ALIASES, required=REQUIRED, expected=COLUMNS))
    settings = await load_settings(db, org_id)
    await lock_codes(db, org_id)

    # The existing catalogue, indexed the ways a row can match it.
    categories = {c.name.strip().lower(): c for c in (await db.scalars(
        select(HardwareCategory).where(HardwareCategory.organization_id == org_id))).all()}
    names = {c.id: key for key, c in categories.items()}
    products = list((await db.scalars(select(HardwareProduct).where(HardwareProduct.organization_id == org_id))).all())
    units = list((await db.scalars(select(HardwareProductUnit).where(HardwareProductUnit.organization_id == org_id))).all())
    by_sku = {p.sku: p for p in products if p.sku}
    by_barcode = {p.barcode: p for p in products if p.barcode}
    by_key = {(names.get(p.category_id, ""), p.name.strip().lower(), (p.size_spec or "").strip().lower()): p for p in products}
    # Who holds each barcode: (product id, unit) with unit None for the product's own (base-unit) barcode.
    holders: Dict[str, Tuple[uuid.UUID, Optional[str]]] = {p.barcode: (p.id, None) for p in products if p.barcode}
    units_of: Dict[uuid.UUID, Dict[str, HardwareProductUnit]] = {}
    for u in units:
        units_of.setdefault(u.product_id, {})[u.unit] = u
        if u.barcode:
            holders[u.barcode] = (u.product_id, u.unit)

    created: Set[uuid.UUID] = set()
    updated: Set[uuid.UUID] = set()
    touched: Set[uuid.UUID] = set()
    categories_created = units_created = units_updated = 0

    for row in rows:
        category = categories.get(row.category.lower())
        product = (by_sku.get(row.sku) if row.sku else None) or (by_barcode.get(row.barcode) if row.barcode else None)
        product = product or by_key.get(row.key)
        product_id = product.id if product is not None else None
        problems: List[str] = []
        if product_id is not None and product_id in touched:
            problems.append("an earlier line already updates this product")
        if row.sku and by_sku.get(row.sku) not in (None, product):
            problems.append(f"sku {row.sku} already belongs to another product")
        for code, unit in ((row.barcode, None), (row.alt_barcode, row.alt_unit)):
            holder = holders.get(code) if code else None
            if holder is not None and holder != (product_id, unit):
                problems.append(f"barcode {code} already belongs to another product or unit")
        base_unit = row.unit or (product.base_unit if product is not None else HardwareUnit.PCS.value)
        if row.alt_unit == base_unit:
            problems.append(f"alt_unit {base_unit} is the same as the base unit")
        elif product is not None and base_unit in units_of.get(product.id, {}):
            problems.append(f"unit {base_unit} is already an alternate unit of this product")
        if problems:
            errors.append(kit.error_row(row.line, problems, row.raw, COLUMNS))
            continue

        if category is None:
            category = HardwareCategory(id=uuid.uuid4(), organization_id=org_id, name=row.category, sort_order=0,
                                        is_active=True)
            if not dry_run:
                db.add(category)
            categories[row.category.lower()] = category
            categories_created += 1

        values: Dict[str, Any] = {
            "name": row.name, "category_id": category.id, "brand": row.brand, "size_spec": row.size, "hsn_code": row.hsn,
            "base_unit": base_unit,
            "allow_fraction": row.allow_fraction if row.allow_fraction is not None else default_fraction(base_unit),
            "price": row.price, "wholesale_price": row.wholesale_price, "min_price": row.min_price, "mrp": row.mrp,
            "is_active": True,
        }
        if product is None:
            product = HardwareProduct(
                id=uuid.uuid4(), organization_id=org_id, sku=row.sku, barcode=row.barcode,
                tax_rate=row.gst_rate if row.gst_rate is not None else settings.default_tax_rate, **values)
            if not dry_run:
                db.add(product)
            created.add(product.id)
        else:
            if not dry_run:
                for key, value in values.items():
                    setattr(product, key, value)
                for key in ("sku", "barcode"):  # a blank code keeps the one the product has
                    if getattr(row, key):
                        setattr(product, key, getattr(row, key))
                if row.gst_rate is not None:
                    product.tax_rate = row.gst_rate
            updated.add(product.id)
        touched.add(product.id)
        if row.sku:
            by_sku[row.sku] = product
        if row.barcode:
            by_barcode[row.barcode] = product
            holders[row.barcode] = (product.id, None)
        by_key[row.key] = product

        if row.alt_unit is not None:
            assert row.alt_factor is not None
            have = units_of.setdefault(product.id, {})
            unit_row = have.get(row.alt_unit)
            if unit_row is None:
                unit_row = HardwareProductUnit(id=uuid.uuid4(), organization_id=org_id, product_id=product.id, unit=row.alt_unit,
                                               factor=row.alt_factor, price=row.alt_price, barcode=row.alt_barcode)
                if not dry_run:
                    db.add(unit_row)
                have[row.alt_unit] = unit_row
                units_created += 1
            else:
                if not dry_run:
                    unit_row.factor, unit_row.price = row.alt_factor, row.alt_price
                    if row.alt_barcode:
                        unit_row.barcode = row.alt_barcode
                units_updated += 1
            if row.alt_barcode:
                holders[row.alt_barcode] = (product.id, row.alt_unit)

    errors.sort(key=lambda e: e["row"])
    result = HardwareImportResult(
        products_created=len(created), products_updated=len(updated - created), units_created=units_created,
        units_updated=units_updated, categories_created=categories_created, errors=errors, dry_run=dry_run,
    )
    if dry_run:
        await db.rollback()
        return result
    await flush_or_conflict(db)  # the unit of work inserts categories, then products, then units (foreign keys)
    await announce(db, org_id, "hardware.catalog.imported",
                   {"products_created": result.products_created, "products_updated": result.products_updated})
    await commit(db)
    return result
