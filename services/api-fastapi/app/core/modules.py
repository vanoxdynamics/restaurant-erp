"""Catalog of sellable ERP modules. Defined in code; plans and per-organization overrides reference the keys."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Dict, FrozenSet, Tuple


class ModuleKey(StrEnum):
    POS = "pos"  # orders, KOTs, billing, floor, menu and staff: the core of the product
    ANALYTICS = "analytics"  # live KPIs, sales breakdowns, Z-reports, audit log
    KDS = "kds"  # kitchen screen, ticket statuses and kitchen staff; without it the kitchen works from printed slips


@dataclass(frozen=True)
class ModuleDef:
    key: ModuleKey
    name: str
    description: str
    is_core: bool = False  # core modules are always on and cannot be disabled
    depends_on: Tuple[ModuleKey, ...] = ()


MODULES: Dict[ModuleKey, ModuleDef] = {
    ModuleKey.POS: ModuleDef(ModuleKey.POS, "Point of sale", "Orders, KOTs, billing, floor, menu and staff", is_core=True),
    ModuleKey.KDS: ModuleDef(
        ModuleKey.KDS, "Kitchen display", "Kitchen screen, ticket statuses and kitchen staff accounts",
        depends_on=(ModuleKey.POS,),
    ),
    ModuleKey.ANALYTICS: ModuleDef(
        ModuleKey.ANALYTICS, "Analytics & reports", "Live KPIs, sales, Z-reports and audit log",
        depends_on=(ModuleKey.POS,),
    ),
}

CORE_MODULES: FrozenSet[str] = frozenset(m.key.value for m in MODULES.values() if m.is_core)
ALL_MODULES: FrozenSet[str] = frozenset(m.value for m in ModuleKey)


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
