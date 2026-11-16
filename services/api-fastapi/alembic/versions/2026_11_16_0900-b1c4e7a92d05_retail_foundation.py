"""retail modules foundation: dress and hardware shop categories, shop audit actions

Organizations may be `apparel` (dress / footwear shop) or `hardware` (hardware / electrical shop); both run their own modules.
The audit log gains `sale_return`, `price_override` and `credit_limit_override`. Only check constraints change: no table,
column or row is touched.

Revision ID: b1c4e7a92d05
Revises: a7d2c9e54f16
Create Date: 2026-11-16 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b1c4e7a92d05'
down_revision: Union[str, None] = 'a7d2c9e54f16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_CATEGORIES = "'restaurant', 'cafe', 'grocery', 'retail'"
NEW_CATEGORIES = OLD_CATEGORIES + ", 'apparel', 'hardware'"
OLD_ACTIONS = ("'kot_void', 'order_cancel', 'order_item_cancel', 'bill_void', 'high_discount', "
               "'table_transfer', 'table_merge', 'login_lockout', 'cash_variance', 'kot_reprint', 'payment_reverse'")
NEW_ACTIONS = OLD_ACTIONS + ", 'sale_return', 'price_override', 'credit_limit_override'"


def _recreate(categories: str, actions: str) -> None:
    op.drop_constraint('ck_organizations_category', 'organizations', type_='check')
    op.create_check_constraint('ck_organizations_category', 'organizations', f"category IN ({categories})")
    op.drop_constraint('ck_audit_logs_action_type', 'audit_logs', type_='check')
    op.create_check_constraint('ck_audit_logs_action_type', 'audit_logs', f"action_type IN ({actions})")


def upgrade() -> None:
    _recreate(NEW_CATEGORIES, NEW_ACTIONS)


def downgrade() -> None:
    # Refuse rather than rewrite data: a dress or hardware shop, or its audit trail, cannot be represented before this revision.
    bind = op.get_bind()
    shops = bind.scalar(sa.text("SELECT count(*) FROM organizations WHERE category IN ('apparel', 'hardware')"))
    actions = bind.scalar(sa.text(
        "SELECT count(*) FROM audit_logs WHERE action_type IN ('sale_return', 'price_override', 'credit_limit_override')"))
    if shops or actions:
        raise RuntimeError(f"Cannot downgrade: {shops} dress/hardware organizations and {actions} shop audit rows exist")
    _recreate(OLD_CATEGORIES, OLD_ACTIONS)
