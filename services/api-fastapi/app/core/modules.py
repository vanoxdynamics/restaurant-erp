"""
Catalog of sellable ERP modules. Defined in code; plans and per-organization overrides reference the keys.

Each module belongs to the business types it serves: a restaurant never gets the dress-shop modules, and a dress shop never
gets the kitchen display. A plan may list modules of several types; a business gets only those of its own type.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Dict, FrozenSet, Optional, Tuple

from app.core.enums import BusinessCategory


class ModuleKey(StrEnum):
    POS = "pos"  # orders, KOTs, billing, floor, menu and staff: the core of the product
    ANALYTICS = "analytics"  # live KPIs, sales breakdowns, Z-reports, audit log
    KDS = "kds"  # kitchen screen, ticket statuses and kitchen staff; without it the kitchen works from printed slips
    INVENTORY = "inventory"  # stock on hand per branch, purchases, adjustments and low-stock alerts
    OFFLINE_BILLING = "offline_billing"  # counter devices keep billing in cash while the internet is down
    APPAREL = "apparel"  # dress shop: products in sizes and colours, stock, counter sales, shift and day reports
    APPAREL_RETURNS = "apparel_returns"  # dress shop: returns, exchanges, credit notes and store credit
    APPAREL_LABELS = "apparel_labels"  # dress shop: barcode sticker sheets
    HARDWARE = "hardware"  # hardware shop: products with alternate units and price tiers, stock and deliveries


_C = BusinessCategory
FOOD: FrozenSet[str] = frozenset({_C.RESTAURANT.value, _C.CAFE.value})  # businesses with a kitchen
COUNTER: FrozenSet[str] = frozenset({_C.RESTAURANT.value, _C.CAFE.value, _C.GROCERY.value, _C.RETAIL.value})  # the menu-based POS


@dataclass(frozen=True)
class ModuleDef:
    key: ModuleKey
    name: str
    description: str
    business_types: FrozenSet[str]  # the business categories this module serves
    is_core: bool = False  # core modules are always on (for their business types) and cannot be disabled
    depends_on: Tuple[ModuleKey, ...] = ()


MODULES: Dict[ModuleKey, ModuleDef] = {
    ModuleKey.POS: ModuleDef(ModuleKey.POS, "Point of sale", "Orders, KOTs, billing, floor, menu and staff", COUNTER, is_core=True),
    ModuleKey.KDS: ModuleDef(
        ModuleKey.KDS, "Kitchen display", "Kitchen screen, ticket statuses and kitchen staff accounts", FOOD,
        depends_on=(ModuleKey.POS,),
    ),
    ModuleKey.ANALYTICS: ModuleDef(
        ModuleKey.ANALYTICS, "Analytics & reports", "Live KPIs, sales, Z-reports and audit log", COUNTER,
        depends_on=(ModuleKey.POS,),
    ),
    ModuleKey.INVENTORY: ModuleDef(
        ModuleKey.INVENTORY, "Inventory", "Stock on hand, purchases, adjustments and low-stock alerts", COUNTER,
        depends_on=(ModuleKey.POS,),
    ),
    ModuleKey.OFFLINE_BILLING: ModuleDef(
        ModuleKey.OFFLINE_BILLING, "Offline billing",
        "Counter devices keep billing in cash when the internet is down, and send the sales later", COUNTER,
        depends_on=(ModuleKey.POS,),
    ),
    ModuleKey.APPAREL: ModuleDef(
        ModuleKey.APPAREL, "Dress shop counter",
        "Products in sizes and colours, stock and deliveries, counter sales and payments, shift and day reports",
        frozenset({_C.APPAREL.value}), is_core=True,
    ),
    ModuleKey.APPAREL_RETURNS: ModuleDef(
        ModuleKey.APPAREL_RETURNS, "Returns & store credit", "Returns and exchanges, credit notes, cash refunds and store credit",
        frozenset({_C.APPAREL.value}), depends_on=(ModuleKey.APPAREL,),
    ),
    ModuleKey.APPAREL_LABELS: ModuleDef(
        ModuleKey.APPAREL_LABELS, "Barcode labels", "Printable sticker sheets with barcode, MRP and price for each piece",
        frozenset({_C.APPAREL.value}), depends_on=(ModuleKey.APPAREL,),
    ),
    ModuleKey.HARDWARE: ModuleDef(
        ModuleKey.HARDWARE, "Hardware shop counter",
        "Products with alternate units and price tiers, barcode lookup, import, stock and deliveries",
        frozenset({_C.HARDWARE.value}), is_core=True,
    ),
}

CORE_MODULES: FrozenSet[str] = frozenset(m.key.value for m in MODULES.values() if m.is_core)
ALL_MODULES: FrozenSet[str] = frozenset(m.value for m in ModuleKey)


def modules_for(category: Optional[str]) -> FrozenSet[str]:
    """Every module a business of this type can have (an unknown or missing type counts as a restaurant)."""
    category = category or _C.RESTAURANT.value
    return frozenset(m.key.value for m in MODULES.values() if category in m.business_types)


def core_modules_for(category: Optional[str]) -> FrozenSet[str]:
    """The modules a business of this type always has."""
    return frozenset(k for k in modules_for(category) if MODULES[ModuleKey(k)].is_core)


def resolve_dependencies(enabled: FrozenSet[str]) -> FrozenSet[str]:
    """Drop unknown modules and modules whose dependencies are not enabled (repeats until stable)."""
    current = set(enabled) & ALL_MODULES
    changed = True
    while changed:
        changed = False
        for key in list(current):
            if any(d.value not in current for d in MODULES[ModuleKey(key)].depends_on):
                current.discard(key)
                changed = True
    return frozenset(current)
