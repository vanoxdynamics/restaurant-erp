from typing import Any, Dict, List

from pydantic import BaseModel, Field


class ItemImportRequest(BaseModel):
    """
    Items as CSV text with a header row. Columns (any order, case-insensitive): name, category, price (required); gst_rate,
    hsn, unit, barcode, sku, sold_by_weight (optional). A row updates the existing item with the same SKU, else the same barcode,
    else the same name in the same category; otherwise it creates one. Missing categories are created.
    """
    csv: str = Field(min_length=1, max_length=5_000_000)
    dry_run: bool = False  # validate and report only


class ItemImportError(BaseModel):
    row: int  # 1-based line number in the file (the header is line 1)
    errors: List[str]
    data: Dict[str, Any] = {}


class ItemImportResult(BaseModel):
    created: int
    updated: int
    categories_created: int
    errors: List[ItemImportError]
    dry_run: bool
