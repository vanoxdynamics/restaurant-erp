"""Request and response bodies of the dress shop module (`/apparel`). Money is a rupee string with 2 decimals."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.enums import (
    ApparelMovementKind,
    ApparelPaymentMode,
    ApparelRefundMode,
    ApparelTaxMode,
    BillStatus,
    PaymentStatus,
    StoreCreditStatus,
)
from app.schemas.billing import PaymentReverse, PrintRowRead, TerminalPaymentComplete
from app.schemas.common import Money, Pin, PositiveMoney, Reason

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
CategoryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Brand = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Size = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
Colour = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[\x21-\x7e]+$")]
Hsn = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{4,8}$")]
Rate = Annotated[Decimal, Field(ge=0, le=40, max_digits=5, decimal_places=2)]

MAX_VARIANTS = 400  # sizes x colours of one style


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- settings ---------------------------------------------------------------------------------------------------------------


class ApparelSettingsRead(ORM):
    prices_include_tax: bool
    round_to_rupee: bool
    low_rate: Decimal
    slab_price: Decimal
    high_rate: Decimal
    return_days: int


class ApparelSettingsUpdate(BaseModel):
    """Fields left out are not changed."""
    prices_include_tax: Optional[bool] = None
    round_to_rupee: Optional[bool] = None
    low_rate: Optional[Rate] = None
    slab_price: Optional[Money] = None
    high_rate: Optional[Rate] = None
    return_days: Optional[int] = Field(default=None, ge=0, le=365)


# --- categories -------------------------------------------------------------------------------------------------------------


class ApparelCategoryRead(ORM):
    id: uuid.UUID
    name: str
    sort_order: int
    is_active: bool


class ApparelCategoryCreate(BaseModel):
    name: CategoryName
    sort_order: int = Field(default=0, ge=0, le=10_000)


class ApparelCategoryUpdate(BaseModel):
    name: Optional[CategoryName] = None
    sort_order: Optional[int] = Field(default=None, ge=0, le=10_000)
    is_active: Optional[bool] = None


# --- products and variants --------------------------------------------------------------------------------------------------


class VariantIn(BaseModel):
    size: Size
    colour: Colour = "Default"
    price: Money
    mrp: Optional[Money] = None
    sku: Optional[Code] = None
    barcode: Optional[Code] = None  # left out: an in-store barcode is generated

    @model_validator(mode="after")
    def _price_within_mrp(self) -> "VariantIn":
        if self.mrp is not None and self.price > self.mrp:
            raise ValueError("price may not exceed MRP")
        return self


class VariantGrid(BaseModel):
    """A product's variants as sizes x colours, all at one price (and MRP); or an explicit list of variants."""
    sizes: List[Size] = Field(default_factory=list, max_length=60)
    colours: List[Colour] = Field(default_factory=list, max_length=60)
    price: Optional[Money] = None
    mrp: Optional[Money] = None
    variants: List[VariantIn] = Field(default_factory=list, max_length=MAX_VARIANTS)

    @model_validator(mode="after")
    def _grid_or_list(self) -> "VariantGrid":
        if self.sizes or self.colours:
            if self.variants:
                raise ValueError("send either sizes and colours or a list of variants, not both")
            if not self.sizes:
                raise ValueError("sizes are required with colours")
            if self.price is None:
                raise ValueError("price is required with sizes")
            if self.mrp is not None and self.price > self.mrp:
                raise ValueError("price may not exceed MRP")
            if len(self.sizes) * max(len(self.colours), 1) > MAX_VARIANTS:
                raise ValueError(f"at most {MAX_VARIANTS} variants per style")
        return self

    def expand(self) -> List[VariantIn]:
        if self.variants:
            return list(self.variants)
        colours = self.colours or ["Default"]
        assert self.price is not None
        return [VariantIn(size=s, colour=c, price=self.price, mrp=self.mrp) for c in colours for s in self.sizes]


class ProductCreate(VariantGrid):
    category_id: uuid.UUID
    name: Name
    brand: Optional[Brand] = None
    style_code: Optional[Code] = None
    hsn_code: Optional[Hsn] = None
    tax_mode: ApparelTaxMode = ApparelTaxMode.SLAB
    tax_rate: Optional[Rate] = None
    description: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _needs_variants_and_rate(self) -> "ProductCreate":
        if not self.sizes and not self.variants:
            raise ValueError("a product needs at least one size")
        if self.tax_mode == ApparelTaxMode.FIXED and self.tax_rate is None:
            raise ValueError("tax_rate is required when tax_mode is fixed")
        return self


class ProductUpdate(BaseModel):
    """Fields left out are not changed. `null` clears an optional field."""
    category_id: Optional[uuid.UUID] = None
    name: Optional[Name] = None
    brand: Optional[Brand] = None
    style_code: Optional[Code] = None
    hsn_code: Optional[Hsn] = None
    tax_mode: Optional[ApparelTaxMode] = None
    tax_rate: Optional[Rate] = None
    description: Optional[str] = Field(default=None, max_length=2000)
    is_active: Optional[bool] = None


class VariantUpdate(BaseModel):
    """Fields left out are not changed. `barcode: null` clears it (a new one is not generated)."""
    size: Optional[Size] = None
    colour: Optional[Colour] = None
    price: Optional[Money] = None
    mrp: Optional[Money] = None
    sku: Optional[Code] = None
    barcode: Optional[Code] = None
    sort_order: Optional[int] = Field(default=None, ge=0, le=100_000)
    is_active: Optional[bool] = None


class VariantRead(ORM):
    id: uuid.UUID
    product_id: uuid.UUID
    size: str
    colour: str
    sku: Optional[str] = None
    barcode: Optional[str] = None
    mrp: Optional[Decimal] = None
    price: Decimal
    sort_order: int
    is_active: bool
    on_hand: Optional[int] = None  # stock in the asked-for branch, when one was asked for


class ProductRead(ORM):
    id: uuid.UUID
    category_id: uuid.UUID
    category_name: Optional[str] = None
    name: str
    brand: Optional[str] = None
    style_code: Optional[str] = None
    hsn_code: Optional[str] = None
    tax_mode: str
    tax_rate: Optional[Decimal] = None
    description: Optional[str] = None
    is_active: bool
    created_at: datetime
    variants: List[VariantRead]


class VariantLookupRead(BaseModel):
    """What a scanned code found: the variant, and its product with all its variants."""
    variant: VariantRead
    product: ProductRead


# --- import -----------------------------------------------------------------------------------------------------------------


class ApparelImportRequest(BaseModel):
    """
    One row per variant. Columns: style_code, name, category, brand, hsn, size, colour, mrp, price, barcode, sku, gst_rate,
    opening_stock. Required: name, category, size, price.
    """
    csv: str = Field(min_length=1, max_length=10_000_000)
    dry_run: bool = False
    branch_id: Optional[uuid.UUID] = None  # where opening_stock goes


class ImportRowError(BaseModel):
    row: int
    errors: List[str]
    data: Dict[str, Any] = {}


class ApparelImportResult(BaseModel):
    products_created: int
    products_updated: int
    variants_created: int
    variants_updated: int
    categories_created: int
    errors: List[ImportRowError]
    dry_run: bool


# --- stock (A2) --------------------------------------------------------------------------------------------------------------

Pieces = Annotated[int, Field(gt=0, le=1_000_000, strict=True)]
Supplier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
InvoiceNo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class ApparelStockRead(BaseModel):
    """A variant's stock in a branch. `cost_price` and `stock_value` go to owners and managers only."""
    variant_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    category_id: uuid.UUID
    category_name: str
    size: str
    colour: str
    sku: Optional[str] = None
    barcode: Optional[str] = None
    price: Decimal
    quantity: int
    low_stock_level: Optional[int] = None
    is_low: bool
    is_out: bool
    cost_price: Optional[Decimal] = None
    stock_value: Optional[Decimal] = None


class ApparelStockSettings(BaseModel):
    """Fields left out are not changed; `null` clears."""
    branch_id: Optional[uuid.UUID] = None
    low_stock_level: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    cost_price: Optional[Money] = None


class ApparelPurchaseLineIn(BaseModel):
    variant_id: uuid.UUID
    quantity: Pieces
    unit_cost: Optional[Money] = None  # what one piece cost; becomes the variant's cost price in the branch


class ApparelPurchaseCreate(BaseModel):
    branch_id: Optional[uuid.UUID] = None
    supplier_name: Optional[Supplier] = None
    invoice_number: Optional[InvoiceNo] = None
    received_on: Optional[date] = None  # defaults to today at the branch
    notes: Optional[str] = Field(default=None, max_length=500)
    lines: List[ApparelPurchaseLineIn] = Field(min_length=1, max_length=1000)


class ApparelPurchaseLineRead(BaseModel):
    variant_id: uuid.UUID
    product_name: str
    size: str
    colour: str
    quantity: int
    unit_cost: Optional[Decimal] = None
    line_cost: Optional[Decimal] = None


class ApparelPurchaseRead(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    supplier_name: Optional[str] = None
    invoice_number: Optional[str] = None
    received_on: date
    notes: Optional[str] = None
    total_cost: Decimal
    created_by: Optional[str] = None
    created_at: datetime
    lines: List[ApparelPurchaseLineRead] = []


class ApparelStockAdjust(BaseModel):
    """`delta` adds or removes pieces; `counted` replaces the quantity with a physical count. Exactly one."""
    branch_id: Optional[uuid.UUID] = None
    variant_id: uuid.UUID
    delta: Optional[int] = Field(default=None, ge=-1_000_000, le=1_000_000, strict=True)
    counted: Optional[int] = Field(default=None, ge=0, le=1_000_000, strict=True)
    reason: Reason

    @model_validator(mode="after")
    def _one_of(self) -> "ApparelStockAdjust":
        if (self.delta is None) == (self.counted is None):
            raise ValueError("send exactly one of delta or counted")
        if self.delta == 0:
            raise ValueError("delta must not be zero")
        return self


class ApparelMovementRead(BaseModel):
    id: uuid.UUID
    variant_id: uuid.UUID
    product_name: str
    size: str
    colour: str
    kind: ApparelMovementKind
    quantity: int
    balance_after: int
    unit_cost: Optional[Decimal] = None
    reason: Optional[str] = None
    purchase_id: Optional[uuid.UUID] = None
    created_by: Optional[str] = None
    created_at: datetime


# --- sales and payments (A3) --------------------------------------------------------------------------------------------------


class ApparelSaleLineIn(BaseModel):
    variant_id: uuid.UUID
    quantity: int = Field(gt=0, le=999)
    unit_price: Optional[Money] = None  # defaults to the variant's price; any other price needs a manager PIN
    line_discount: Money = Decimal("0.00")


class ApparelSaleCreate(BaseModel):
    branch_id: Optional[uuid.UUID] = None
    lines: List[ApparelSaleLineIn] = Field(min_length=1, max_length=200)
    bill_discount: Money = Decimal("0.00")
    discount_reason: Optional[str] = Field(default=None, max_length=500)
    customer_id: Optional[uuid.UUID] = None
    salesperson_id: Optional[uuid.UUID] = None
    client_sale_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    approver_pin: Optional[Pin] = None
    notes: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_discount_reason(self) -> "ApparelSaleCreate":
        discounted = self.bill_discount > 0 or any(line.line_discount > 0 for line in self.lines)
        if discounted and not (self.discount_reason and self.discount_reason.strip()):
            raise ValueError("discount_reason is required when a discount is applied")
        return self


class ApparelVoid(BaseModel):
    reason: Reason
    approver_pin: Optional[Pin] = None


class ApparelSaleLineRead(ORM):
    id: uuid.UUID
    variant_id: uuid.UUID
    name_snapshot: str
    size_snapshot: str
    colour_snapshot: str
    hsn_snapshot: Optional[str] = None
    barcode_snapshot: Optional[str] = None
    quantity: int
    mrp_snapshot: Optional[Decimal] = None
    unit_price: Decimal
    line_discount: Decimal
    discount: Decimal
    tax_rate: Decimal
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    line_total: Decimal
    returned_quantity: int


class ApparelPaymentCreate(BaseModel):
    payment_mode: ApparelPaymentMode
    amount: PositiveMoney
    reference_id: Optional[str] = Field(default=None, max_length=100)
    # Store credit: the code printed on the credit note. `amount` is at most what the customer wants to spend.
    credit_note_code: Optional[str] = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def check_reference(self) -> "ApparelPaymentCreate":
        if self.payment_mode in (ApparelPaymentMode.UPI, ApparelPaymentMode.CARD) and not self.reference_id:
            raise ValueError("reference_id is required for upi and card payments")
        if self.payment_mode == ApparelPaymentMode.CREDIT_NOTE and not (self.credit_note_code and self.credit_note_code.strip()):
            raise ValueError("credit_note_code is required for store credit")
        return self


class ApparelSplitPayment(BaseModel):
    payments: List[ApparelPaymentCreate] = Field(min_length=2, max_length=10)


class ApparelTerminalStart(BaseModel):
    payment_mode: Literal[ApparelPaymentMode.CARD, ApparelPaymentMode.UPI]
    amount: PositiveMoney
    provider: Literal["pinelabs"] = "pinelabs"


ApparelTerminalComplete = TerminalPaymentComplete
ApparelPaymentReverse = PaymentReverse


class ApparelPaymentRead(ORM):
    id: uuid.UUID
    sale_id: uuid.UUID
    payment_mode: ApparelPaymentMode
    amount: Decimal
    reference_id: Optional[str] = None
    status: PaymentStatus
    provider: str = "manual"
    provider_meta: Optional[Dict[str, Any]] = None
    created_at: datetime


class ApparelSaleRead(ORM):
    id: uuid.UUID
    branch_id: uuid.UUID
    sale_number: str
    business_date: date
    customer_id: Optional[uuid.UUID] = None
    salesperson_id: Optional[uuid.UUID] = None
    created_by_id: Optional[uuid.UUID] = None
    subtotal: Decimal
    discount_amount: Decimal
    discount_reason: Optional[str] = None
    taxable_value: Decimal
    tax_cgst: Decimal
    tax_sgst: Decimal
    tax_igst: Decimal
    round_off: Decimal
    grand_total: Decimal
    paid_amount: Decimal
    balance_due: Decimal
    status: BillStatus
    settled_at: Optional[datetime] = None
    void_reason: Optional[str] = None
    client_sale_id: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    lines: List[ApparelSaleLineRead] = []
    payments: List[ApparelPaymentRead] = []


class ApparelPaymentResult(BaseModel):
    payment: ApparelPaymentRead
    sale: ApparelSaleRead
    change_due: Decimal = Decimal("0.00")


class ApparelReceiptRead(BaseModel):
    sale_number: str
    paper_mm: int
    rows: List[PrintRowRead]
    text: str
    logo_data_url: Optional[str] = None


# --- returns and store credit (A5) --------------------------------------------------------------------------------------------


class ApparelReturnLineIn(BaseModel):
    sale_line_id: uuid.UUID
    quantity: int = Field(gt=0, le=999)
    restock: bool = True  # false for damaged goods that do not go back on the shelf


class ApparelReturnCreate(BaseModel):
    sale_id: uuid.UUID
    lines: List[ApparelReturnLineIn] = Field(min_length=1, max_length=200)
    reason: Reason
    refund_mode: ApparelRefundMode
    client_return_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    approver_pin: Optional[Pin] = None


class ApparelReturnLineRead(ORM):
    id: uuid.UUID
    sale_line_id: uuid.UUID
    quantity: int
    amount: Decimal
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    restock: bool


class ApparelReturnRead(ORM):
    id: uuid.UUID
    branch_id: uuid.UUID
    return_number: str
    sale_id: uuid.UUID
    business_date: date
    reason: str
    refund_mode: ApparelRefundMode
    total_amount: Decimal
    taxable_value: Decimal
    tax_cgst: Decimal
    tax_sgst: Decimal
    tax_igst: Decimal
    created_at: datetime
    lines: List[ApparelReturnLineRead] = []
    store_credit_code: Optional[str] = None


class ApparelStoreCreditRead(ORM):
    id: uuid.UUID
    code: str
    customer_id: Optional[uuid.UUID] = None
    amount: Decimal
    balance: Decimal
    status: StoreCreditStatus
    created_at: datetime


# --- labels and reports (A6) --------------------------------------------------------------------------------------------------


class ApparelLabelItem(BaseModel):
    variant_id: uuid.UUID
    copies: int = Field(default=1, ge=1, le=200)


class ApparelLabelsRequest(BaseModel):
    items: List[ApparelLabelItem] = Field(min_length=1, max_length=200)
    columns: int = Field(default=3, ge=1, le=6)
    label_height_mm: int = Field(default=30, ge=15, le=80)


class ReportSales(BaseModel):
    count: int
    units: int
    subtotal: Decimal
    discount_amount: Decimal
    taxable_value: Decimal
    tax_cgst: Decimal
    tax_sgst: Decimal
    tax_igst: Decimal
    round_off: Decimal
    grand_total: Decimal


class ReportTaxRate(BaseModel):
    rate: Decimal
    taxable_value: Decimal
    tax: Decimal


class ReportPayment(BaseModel):
    mode: str
    count: int
    amount: Decimal


class ReportReturns(BaseModel):
    count: int
    total: Decimal
    cash_total: Decimal
    store_credit_total: Decimal


class ReportProduct(BaseModel):
    name: str
    units: int
    value: Decimal


class ReportSize(BaseModel):
    size: str
    units: int


class ApparelDayReport(BaseModel):
    branch_id: uuid.UUID
    date: date
    sales: ReportSales
    tax_by_rate: List[ReportTaxRate]
    payments: List[ReportPayment]
    returns: ReportReturns
    net_cash: Decimal
    top_products: List[ReportProduct]
    units_by_size: List[ReportSize]


class ReportProductSize(BaseModel):
    size: str
    units: int
    value: Decimal


class ReportProductRow(BaseModel):
    name: str
    units: int
    value: Decimal
    sizes: List[ReportProductSize]


class ApparelProductSales(BaseModel):
    branch_id: uuid.UUID
    date_from: date
    date_to: date
    units: int
    value: Decimal
    products: List[ReportProductRow]
