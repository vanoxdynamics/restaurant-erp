"""
The dress shop module (business category `apparel`): styles in sizes and colours, their stock, sales, payments and returns.

Plan: docs/retail-modules/01-apparel.md. It never imports from `app.modules.hardware` and never writes to the shared orders,
bills, payments, menu or stock tables. Shared shop helpers come from `app.modules.retailkit`.
"""

from app.core.cash import register_cash_source  # noqa: E402
from app.modules.apparel.sales import cash_source  # noqa: E402

register_cash_source(cash_source)
