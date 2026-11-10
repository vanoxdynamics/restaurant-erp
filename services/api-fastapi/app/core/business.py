"""
Business categories and the workflow settings they preset.

A category is a preset, not a product: it switches workflow steps on or off. Each organization stores only its overrides
(`organizations.settings`), so changing a category's preset here changes every organization that has not overridden it.
Paid features stay modules (app.core.modules); `kitchen_tickets` decides whether a sale makes KOTs at all, and the `kds`
module decides whether those KOTs go to a screen or to paper.
"""

from typing import Any, Dict, Mapping, Optional

from app.core.enums import BusinessCategory

TABLE_SERVICE = "table_service"  # floor, tables, guests, waiters' phones
KITCHEN_TICKETS = "kitchen_tickets"  # a sale makes KOTs for the kitchen
QUICK_SALE = "quick_sale"  # the counter opens on the quick-sale screen
BARCODE = "barcode"  # items are scanned
WEIGHED_ITEMS = "weighed_items"  # loose items sold by weight or volume
TOKENS = "tokens"  # a daily token number on counter sales, printed large on the bill and KOT

SETTING_KEYS = (TABLE_SERVICE, KITCHEN_TICKETS, QUICK_SALE, BARCODE, WEIGHED_ITEMS, TOKENS)

SETTING_LABELS: Dict[str, str] = {
    TABLE_SERVICE: "Table service",
    KITCHEN_TICKETS: "Kitchen tickets (KOT)",
    QUICK_SALE: "Quick sale home screen",
    BARCODE: "Barcode scanning",
    WEIGHED_ITEMS: "Weighed / loose items",
    TOKENS: "Token numbers",
}

CATEGORY_LABELS: Dict[str, str] = {
    BusinessCategory.RESTAURANT.value: "Restaurant",
    BusinessCategory.CAFE.value: "Café / QSR / Bakery",
    BusinessCategory.GROCERY.value: "Grocery / Kirana",
    BusinessCategory.RETAIL.value: "General retail",
}

PRESETS: Dict[str, Dict[str, bool]] = {
    BusinessCategory.RESTAURANT.value: {TABLE_SERVICE: True, KITCHEN_TICKETS: True, QUICK_SALE: False, BARCODE: False,
                                        WEIGHED_ITEMS: False, TOKENS: False},
    BusinessCategory.CAFE.value: {TABLE_SERVICE: False, KITCHEN_TICKETS: True, QUICK_SALE: True, BARCODE: False,
                                  WEIGHED_ITEMS: True, TOKENS: True},
    BusinessCategory.GROCERY.value: {TABLE_SERVICE: False, KITCHEN_TICKETS: False, QUICK_SALE: True, BARCODE: True,
                                     WEIGHED_ITEMS: True, TOKENS: False},
    BusinessCategory.RETAIL.value: {TABLE_SERVICE: False, KITCHEN_TICKETS: False, QUICK_SALE: True, BARCODE: True,
                                    WEIGHED_ITEMS: False, TOKENS: False},
}


def clean_overrides(overrides: Optional[Mapping[str, Any]]) -> Dict[str, bool]:
    """Keep only known keys with boolean values (anything else in the column is ignored)."""
    return {k: v for k, v in (overrides or {}).items() if k in SETTING_KEYS and isinstance(v, bool)}


def effective_settings(category: Optional[str], overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, bool]:
    preset = PRESETS.get(category or "", PRESETS[BusinessCategory.RESTAURANT.value])
    return {**preset, **clean_overrides(overrides)}
