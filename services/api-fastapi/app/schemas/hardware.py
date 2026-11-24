"""
Request and response bodies of the hardware shop module (`/hardware`).

Money is a rupee string. Prices per base unit carry up to 4 decimals (a screw out of a box of 1,000 costs 0.045); prices per
alternate unit and every amount on an invoice carry 2.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.enums import HardwareMovementKind, HardwareUnit
from app.schemas.common import Money, QuantityOut, Reason

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
CategoryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Brand = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
SizeSpec = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[\x21-\x7e]+$")]
Hsn = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{4,8}$")]
StateCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{2}$")]
Rate = Annotated[Decimal, Field(ge=0, le=40, max_digits=5, decimal_places=2)]
BasePrice = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=4)]  # per base unit
Factor = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=4)]

MAX_UNITS = 10  # alternate units of one product


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- settings ---------------------------------------------------------------------------------------------------------------


class HardwareSettingsRead(ORM):
    prices_include_tax: bool
    round_to_rupee: bool
    default_tax_rate: Decimal
    estimate_valid_days: int
    state_code: Optional[str] = None


class HardwareSettingsUpdate(BaseModel):
    """Fields left out are not changed. `state_code: null` clears it; the other fields cannot be empty."""
    prices_include_tax: Optional[bool] = None
    round_to_rupee: Optional[bool] = None
    default_tax_rate: Optional[Rate] = None
    estimate_valid_days: Optional[int] = Field(default=None, ge=1, le=365)
    state_code: Optional[StateCode] = None


# --- categories -------------------------------------------------------------------------------------------------------------


class HardwareCategoryRead(ORM):
    id: uuid.UUID
    name: str
    sort_order: int
    is_active: bool


class HardwareCategoryCreate(BaseModel):
    name: CategoryName
    sort_order: int = Field(default=0, ge=0, le=10_000)


class HardwareCategoryUpdate(BaseModel):
    name: Optional[CategoryName] = None
    sort_order: Optional[int] = Field(default=None, ge=0, le=10_000)
    is_active: Optional[bool] = None


# --- products and units -----------------------------------------------------------------------------------------------------


class HardwareUnitIn(BaseModel):
    """An alternate unit: `factor` base units make one. Prices are per this unit; left out, they follow the base price."""
    unit: HardwareUnit
    factor: Factor
    price: Optional[Money] = None
    wholesale_price: Optional[Money] = None
    min_price: Optional[Money] = None
    barcode: Optional[Code] = None


def _distinct_units(units: List[HardwareUnitIn]) -> None:
    seen = set()
    for u in units:
        if u.unit in seen:
            raise ValueError(f"unit {u.unit.value} is listed twice")
        seen.add(u.unit)


class HardwareUnitsReplace(BaseModel):
    """The whole list of a product's alternate units: units left out are removed."""
    units: List[HardwareUnitIn] = Field(default_factory=list, max_length=MAX_UNITS)

    @model_validator(mode="after")
    def _distinct(self) -> "HardwareUnitsReplace":
        _distinct_units(self.units)
        return self


class HardwareProductCreate(BaseModel):
    category_id: uuid.UUID
    name: Name
    brand: Optional[Brand] = None
    size_spec: Optional[SizeSpec] = None
    sku: Optional[Code] = None
    barcode: Optional[Code] = None
    hsn_code: Optional[Hsn] = None
    tax_rate: Optional[Rate] = None  # left out: the settings' default rate
    base_unit: HardwareUnit = HardwareUnit.PCS
    allow_fraction: Optional[bool] = None  # left out: true for measured units (m, kg, l...), false for counted ones
    price: BasePrice
    wholesale_price: Optional[BasePrice] = None
    min_price: Optional[BasePrice] = None
    mrp: Optional[BasePrice] = None
    units: List[HardwareUnitIn] = Field(default_factory=list, max_length=MAX_UNITS)

    @model_validator(mode="after")
    def _units(self) -> "HardwareProductCreate":
        _distinct_units(self.units)
        if any(u.unit == self.base_unit for u in self.units):
            raise ValueError("an alternate unit cannot be the base unit")
        return self


class HardwareProductUpdate(BaseModel):
    """Fields left out are not changed. `null` clears an optional field."""
    category_id: Optional[uuid.UUID] = None
    name: Optional[Name] = None
    brand: Optional[Brand] = None
    size_spec: Optional[SizeSpec] = None
    sku: Optional[Code] = None
    barcode: Optional[Code] = None
    hsn_code: Optional[Hsn] = None
    tax_rate: Optional[Rate] = None
    base_unit: Optional[HardwareUnit] = None
    allow_fraction: Optional[bool] = None
    price: Optional[BasePrice] = None
    wholesale_price: Optional[BasePrice] = None
    min_price: Optional[BasePrice] = None
    mrp: Optional[BasePrice] = None
    is_active: Optional[bool] = None


class HardwareUnitRead(ORM):
    """An alternate unit as stored (null prices follow the base price; see `options` for what they come to)."""
    id: uuid.UUID
    unit: HardwareUnit
    factor: Decimal
    price: Optional[Decimal] = None
    wholesale_price: Optional[Decimal] = None
    min_price: Optional[Decimal] = None
    barcode: Optional[str] = None


class HardwareUnitOption(BaseModel):
    """One way to sell the product (the base unit first), with every price worked out per this unit."""
    unit: HardwareUnit
    factor: Decimal
    is_base: bool
    barcode: Optional[str] = None
    retail_price: Decimal
    wholesale_price: Decimal
    retail_floor: Optional[Decimal] = None  # below it a cashier needs a manager's PIN
    wholesale_floor: Optional[Decimal] = None
    mrp: Optional[Decimal] = None  # tax included


class HardwareProductRead(ORM):
    id: uuid.UUID
    category_id: uuid.UUID
    category_name: Optional[str] = None
    name: str
    brand: Optional[str] = None
    size_spec: Optional[str] = None
    sku: Optional[str] = None
    barcode: Optional[str] = None
    hsn_code: Optional[str] = None
    tax_rate: Decimal
    base_unit: HardwareUnit
    allow_fraction: bool
    price: Decimal
    wholesale_price: Optional[Decimal] = None
    min_price: Optional[Decimal] = None
    mrp: Optional[Decimal] = None
    is_active: bool
    created_at: datetime
    units: List[HardwareUnitRead]
    options: List[HardwareUnitOption]
    on_hand: Optional[QuantityOut] = None  # base units in the branch asked about; null when no branch was named


class HardwareLookupRead(BaseModel):
    """What a scanned code found: the product, and the unit the code belongs to (a box barcode adds a box)."""
    product: HardwareProductRead
    unit: HardwareUnitOption


# --- import -----------------------------------------------------------------------------------------------------------------


class HardwareImportRequest(BaseModel):
    """
    One row per product. Columns: name, category, brand, size, price, wholesale_price, min_price, mrp, gst_rate, hsn, unit,
    allow_fraction, barcode, sku, alt_unit, alt_factor, alt_price, alt_barcode. Required: name, category, price.
    """
    csv: str = Field(min_length=1, max_length=10_000_000)
    dry_run: bool = False


class HardwareImportRowError(BaseModel):
    row: int
    errors: List[str]
    data: Dict[str, Any] = {}


class HardwareImportResult(BaseModel):
    products_created: int
    products_updated: int
    units_created: int
    units_updated: int
    categories_created: int
    errors: List[HardwareImportRowError]
    dry_run: bool


# --- stock (H2) -------------------------------------------------------------------------------------------------------------

Supplier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
InvoiceNo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
# A quantity typed by a person: up to 3 decimals, as much as a pallet of screws.
Qty = Annotated[Decimal, Field(gt=0, le=10_000_000, max_digits=11, decimal_places=3)]
QtyDelta = Annotated[Decimal, Field(ge=-10_000_000, le=10_000_000, max_digits=11, decimal_places=3)]
QtyCounted = Annotated[Decimal, Field(ge=0, le=10_000_000, max_digits=11, decimal_places=3)]
CostPerUnit = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=4)]


class HardwareStockRead(BaseModel):
    """A product's stock in a branch, in its base unit. `cost_price` and `stock_value` go to owners and managers only."""
    product_id: uuid.UUID
    product_name: str
    brand: Optional[str] = None
    size_spec: Optional[str] = None
    category_id: uuid.UUID
    category_name: str
    sku: Optional[str] = None
    barcode: Optional[str] = None
    base_unit: HardwareUnit
    allow_fraction: bool
    price: Decimal
    quantity: QuantityOut
    low_stock_level: Optional[QuantityOut] = None
    is_low: bool
    is_out: bool
    cost_price: Optional[Decimal] = None
    stock_value: Optional[Decimal] = None
    units: List[HardwareUnitRead] = []


class HardwareStockSettings(BaseModel):
    """Fields left out are not changed; `null` clears. `cost_price` is per base unit."""
    branch_id: Optional[uuid.UUID] = None
    low_stock_level: Optional[QtyCounted] = None
    cost_price: Optional[CostPerUnit] = None


class HardwarePurchaseLineIn(BaseModel):
    """
    Goods received in `unit` (the base unit when left out, or one of the product's alternate units). `unit_cost` is what one of
    that unit cost; the service works out the cost per base unit.
    """
    product_id: uuid.UUID
    unit: Optional[HardwareUnit] = None
    quantity: Qty
    unit_cost: Optional[CostPerUnit] = None


class HardwarePurchaseCreate(BaseModel):
    branch_id: Optional[uuid.UUID] = None
    supplier_name: Optional[Supplier] = None
    invoice_number: Optional[InvoiceNo] = None
    received_on: Optional[date] = None  # defaults to today at the branch
    notes: Optional[str] = Field(default=None, max_length=500)
    lines: List[HardwarePurchaseLineIn] = Field(min_length=1, max_length=1000)


class HardwarePurchaseLineRead(BaseModel):
    product_id: uuid.UUID
    product_name: str
    size_spec: Optional[str] = None
    unit: HardwareUnit  # what was typed
    quantity: QuantityOut  # as typed, in `unit`
    unit_cost: Optional[Decimal] = None  # per `unit`
    line_cost: Optional[Decimal] = None
    base_unit: HardwareUnit
    base_quantity: QuantityOut  # what stock gained
    base_unit_cost: Optional[Decimal] = None


class HardwarePurchaseRead(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    supplier_name: Optional[str] = None
    invoice_number: Optional[str] = None
    received_on: date
    notes: Optional[str] = None
    total_cost: Decimal
    created_by: Optional[str] = None
    created_at: datetime
    lines: List[HardwarePurchaseLineRead] = []


class HardwareStockAdjust(BaseModel):
    """`delta` adds or removes base units; `counted` replaces the quantity with a physical count. Exactly one."""
    branch_id: Optional[uuid.UUID] = None
    product_id: uuid.UUID
    delta: Optional[QtyDelta] = None
    counted: Optional[QtyCounted] = None
    reason: Reason

    @model_validator(mode="after")
    def _one_of(self) -> "HardwareStockAdjust":
        if (self.delta is None) == (self.counted is None):
            raise ValueError("send exactly one of delta or counted")
        if self.delta == 0:
            raise ValueError("delta must not be zero")
        return self


class HardwareMovementRead(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    size_spec: Optional[str] = None
    base_unit: HardwareUnit
    kind: HardwareMovementKind
    quantity: QuantityOut
    balance_after: QuantityOut
    unit_cost: Optional[Decimal] = None
    reason: Optional[str] = None
    purchase_id: Optional[uuid.UUID] = None
    created_by: Optional[str] = None
    created_at: datetime
