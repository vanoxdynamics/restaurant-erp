"""Request and response bodies of the dress shop module (`/apparel`). Money is a rupee string with 2 decimals."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.enums import ApparelTaxMode
from app.schemas.common import Money

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
