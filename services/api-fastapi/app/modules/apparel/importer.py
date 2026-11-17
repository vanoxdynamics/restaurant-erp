"""
Loading a dress shop's catalogue from a spreadsheet: one row per variant (a size and colour of a style).

Rows belong to the same product when their `style_code` matches or, without one, when category and name match. A row matches
an existing variant by SKU, then barcode, then (product, size, colour). Every row is checked first; bad rows are reported with
their line number and skipped, the good ones are saved together in one transaction. `dry_run` saves nothing.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, resolve_branch_id
from app.core.enums import ApparelMovementKind, ApparelTaxMode
from app.core.events import commit
from app.models.apparel import ApparelCategory, ApparelProduct, ApparelVariant
from app.modules.apparel import stock as stocks
from app.modules.apparel.catalog import GridOrder, allocate_barcodes, announce, flush_or_conflict
from app.modules.retailkit import csvimport as kit
from app.schemas.apparel import ApparelImportResult

COLUMNS = ("style_code", "name", "category", "brand", "hsn", "size", "colour", "mrp", "price", "barcode", "sku", "gst_rate",
           "opening_stock")
REQUIRED = ("name", "category", "size", "price")
ALIASES = {
    "style": "style_code", "style_no": "style_code", "style_number": "style_code", "article": "style_code",
    "product": "name", "item": "name", "item_name": "name", "product_name": "name",
    "color": "colour", "shade": "colour", "mrp_price": "mrp", "selling_price": "price", "sale_price": "price", "rate": "price",
    "ean": "barcode", "gst": "gst_rate", "tax": "gst_rate", "tax_rate": "gst_rate", "hsn_code": "hsn", "hsn_sac": "hsn",
    "stock": "opening_stock", "qty": "opening_stock", "quantity": "opening_stock", "opening_qty": "opening_stock",
}


@dataclass
class Row:
    line: int
    name: str
    category: str
    size: str
    colour: str
    price: Decimal
    style_code: Optional[str] = None
    brand: Optional[str] = None
    hsn: Optional[str] = None
    mrp: Optional[Decimal] = None
    barcode: Optional[str] = None
    sku: Optional[str] = None
    gst_rate: Optional[Decimal] = None
    opening_stock: Optional[int] = None
    raw: Dict[str, str] = field(default_factory=dict)

    @property
    def group(self) -> Tuple[str, ...]:
        if self.style_code:
            return ("style", self.style_code.lower())
        return ("name", self.category.lower(), self.name.lower())


def parse(line: int, raw: Dict[str, str]) -> Tuple[Optional[Row], List[str]]:
    errors = kit.RowErrors()
    name = kit.text(raw, "name", errors, max_len=150, required=True)
    category = kit.text(raw, "category", errors, max_len=100, required=True)
    size = kit.text(raw, "size", errors, max_len=20, required=True)
    colour = kit.text(raw, "colour", errors, max_len=40) or "Default"
    brand = kit.text(raw, "brand", errors, max_len=100)
    style_code = kit.code(raw, "style_code", errors)
    hsn = kit.hsn(raw, "hsn", errors)
    price = kit.money(raw, "price", errors, required=True)
    mrp = kit.money(raw, "mrp", errors)
    barcode = kit.code(raw, "barcode", errors)
    sku = kit.code(raw, "sku", errors)
    gst = kit.number(raw, "gst_rate", errors, places=2)
    if gst is not None and gst > 40:
        errors.add(f"gst_rate '{raw.get('gst_rate')}' must be between 0 and 40")
    stock = kit.number(raw, "opening_stock", errors, places=0)
    if price is not None and mrp is not None and price > mrp:
        errors.add(f"price {price} is more than the MRP {mrp}")
    if errors:
        return None, errors.errors
    assert name and category and size and price is not None
    return Row(line=line, name=name, category=category, size=size, colour=colour, price=price, style_code=style_code,
               brand=brand, hsn=hsn, mrp=mrp, barcode=barcode, sku=sku, gst_rate=gst,
               opening_stock=int(stock) if stock is not None else None, raw=raw), []


async def import_catalogue(db: AsyncSession, user: CurrentUser, text: str, dry_run: bool,
                           branch_id: Optional[uuid.UUID]) -> ApparelImportResult:
    org_id = user.organization_id
    if branch_id is not None:
        resolve_branch_id(user, branch_id)
    raw_rows = kit.read_rows(text, aliases=ALIASES, required=REQUIRED, expected=COLUMNS)

    errors: List[Dict[str, Any]] = []
    rows: List[Row] = []
    seen_codes: Dict[Tuple[str, str], int] = {}
    seen_combo: Dict[Tuple[Tuple[str, ...], str, str], int] = {}
    first_of_group: Dict[Tuple[str, ...], Row] = {}
    for line, raw in raw_rows:
        row, problems = parse(line, raw)
        if row is not None:
            for key in ("barcode", "sku"):
                value = getattr(row, key)
                if value:
                    first = seen_codes.setdefault((key, value), line)
                    if first != line:
                        problems.append(f"{key} {value} is also on line {first}")
            combo = (row.group, row.size.lower(), row.colour.lower())
            first = seen_combo.setdefault(combo, line)
            if first != line:
                problems.append(f"size {row.size} in {row.colour} is also on line {first}")
            if row.opening_stock and branch_id is None:
                problems.append("opening_stock needs a branch: choose the branch the stock is in")
            lead = first_of_group.setdefault(row.group, row)
            if lead is not row and lead.gst_rate != row.gst_rate:
                problems.append(f"gst_rate differs from line {lead.line} of the same product")
        if problems:
            errors.append(kit.error_row(line, problems, raw, COLUMNS))
        elif row is not None:
            rows.append(row)

    # The existing catalogue, indexed the ways a row can match it.
    categories = {c.name.strip().lower(): c for c in (await db.scalars(
        select(ApparelCategory).where(ApparelCategory.organization_id == org_id))).all()}
    products = list((await db.scalars(select(ApparelProduct).where(ApparelProduct.organization_id == org_id))).all())
    variants = list((await db.scalars(select(ApparelVariant).where(ApparelVariant.organization_id == org_id))).all())
    by_style = {p.style_code.lower(): p for p in products if p.style_code}
    by_name = {(p.category_id, p.name.strip().lower()): p for p in products}
    by_sku = {v.sku: v for v in variants if v.sku}
    by_barcode = {v.barcode: v for v in variants if v.barcode}
    by_combo = {(v.product_id, v.size.lower(), v.colour.lower()): v for v in variants}
    by_product: Dict[uuid.UUID, List[ApparelVariant]] = {}
    for v in variants:
        by_product.setdefault(v.product_id, []).append(v)
    grid_order = {product_id: GridOrder(vs) for product_id, vs in by_product.items()}

    created_products: set = set()
    updated_products: set = set()
    categories_created = variants_created = variants_updated = 0
    product_for_group: Dict[Tuple[str, ...], ApparelProduct] = {}
    touched_variants: set = set()
    need_barcode: List[ApparelVariant] = []
    opening: List[Tuple[ApparelVariant, int]] = []  # pieces on the shelf, for variants this import creates

    for row in rows:
        category = categories.get(row.category.lower())
        if category is None:
            category = ApparelCategory(id=uuid.uuid4(), organization_id=org_id, name=row.category, sort_order=0, is_active=True)
            if not dry_run:
                db.add(category)
            categories[row.category.lower()] = category
            categories_created += 1

        product = product_for_group.get(row.group)
        if product is None:
            product = by_style.get(row.style_code.lower()) if row.style_code else None
            product = product or by_name.get((category.id, row.name.lower()))
        variant = (by_sku.get(row.sku) if row.sku else None) or (by_barcode.get(row.barcode) if row.barcode else None)
        problem = None
        if variant is not None and product is not None and variant.product_id != product.id:
            problem = f"{'sku ' + row.sku if row.sku and by_sku.get(row.sku) is variant else 'barcode ' + str(row.barcode)} " \
                      f"belongs to another product"
        if variant is None and product is not None:
            variant = by_combo.get((product.id, row.size.lower(), row.colour.lower()))
        for key, index in (("barcode", by_barcode), ("sku", by_sku)):
            value = getattr(row, key)
            holder = index.get(value) if value else None
            if holder is not None and holder is not variant:
                problem = f"{key} {value} already belongs to another variant"
        if problem is None and variant is not None and product is not None:
            # Matched by a code, but the row's size and colour are another variant of the style: renaming would clash.
            other = by_combo.get((product.id, row.size.lower(), row.colour.lower()))
            if other is not None and other is not variant:
                problem = f"size {row.size} in {row.colour} is already another variant of this product"
        if problem is None and variant is not None and id(variant) in touched_variants:
            problem = "an earlier line already updates this variant"
        if problem:
            errors.append(kit.error_row(row.line, [problem], row.raw, COLUMNS))
            continue

        tax_mode = ApparelTaxMode.FIXED.value if row.gst_rate is not None else ApparelTaxMode.SLAB.value
        product_values = {"name": row.name, "category_id": category.id, "brand": row.brand, "style_code": row.style_code,
                          "hsn_code": row.hsn, "tax_mode": tax_mode, "tax_rate": row.gst_rate, "is_active": True}
        if product is None:
            product = ApparelProduct(id=uuid.uuid4(), organization_id=org_id, **product_values)
            if not dry_run:
                db.add(product)
            created_products.add(product.id)
            if row.style_code:
                by_style[row.style_code.lower()] = product
            by_name[(category.id, row.name.lower())] = product
        elif row.group not in product_for_group:
            if not dry_run:
                for key, value in product_values.items():
                    setattr(product, key, value)
            if product.id not in created_products:
                updated_products.add(product.id)
        product_for_group[row.group] = product

        order = grid_order.setdefault(product.id, GridOrder())
        values = {"size": row.size, "colour": row.colour, "price": row.price, "mrp": row.mrp, "sku": row.sku,
                  "is_active": True}
        if variant is None:
            variant = ApparelVariant(id=uuid.uuid4(), organization_id=org_id, product_id=product.id, barcode=row.barcode,
                                     sort_order=order(row.size, row.colour), **values)
            if not dry_run:
                db.add(variant)
            if not row.barcode:
                need_barcode.append(variant)
            if row.opening_stock:
                opening.append((variant, row.opening_stock))
            variants_created += 1
            by_combo[(product.id, row.size.lower(), row.colour.lower())] = variant
        else:
            by_combo.pop((variant.product_id, variant.size.lower(), variant.colour.lower()), None)
            by_combo[(product.id, row.size.lower(), row.colour.lower())] = variant
            if not dry_run:
                for key, value in values.items():
                    if key == "sku" and value is None:
                        continue  # a blank SKU column keeps the SKU the variant has
                    setattr(variant, key, value)
                if row.barcode:
                    variant.barcode = row.barcode
            variants_updated += 1
        touched_variants.add(id(variant))
        for key, index in (("sku", by_sku), ("barcode", by_barcode)):
            value = getattr(row, key)
            if value:
                index[value] = variant

    errors.sort(key=lambda e: e["row"])
    result = ApparelImportResult(
        products_created=len(created_products), products_updated=len(updated_products), variants_created=variants_created,
        variants_updated=variants_updated, categories_created=categories_created, errors=errors, dry_run=dry_run,
    )
    if dry_run:
        await db.rollback()
        return result

    await db.flush()  # categories and products first, so the variants' foreign keys hold
    codes = await allocate_barcodes(db, org_id, len(need_barcode), by_barcode.keys())
    for variant, code in zip(need_barcode, codes, strict=True):
        variant.barcode = code
    await flush_or_conflict(db)
    if opening and branch_id is not None:
        # Opening stock is a count: the shelf held this many when the shop started using the system.
        levels = await stocks.lock_levels(db, branch_id, [v.id for v, _ in opening])
        for variant, pieces in opening:
            stocks.move(db, levels[variant.id], ApparelMovementKind.COUNT, pieces, user_id=user.staff_id,
                        reason="Opening stock (import)")
        stocks.announce(db, branch_id, levels)
    await announce(db, org_id, "apparel.catalog.imported",
                   {"products_created": result.products_created, "variants_created": variants_created})
    await commit(db)
    return result
