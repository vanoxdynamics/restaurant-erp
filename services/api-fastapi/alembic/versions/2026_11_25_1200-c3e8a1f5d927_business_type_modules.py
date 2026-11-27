"""modules per business type: dress shop and hardware shop modules

Adds the module keys `apparel`, `apparel_returns`, `apparel_labels` and `hardware`. The two shop counters are core for their
business type and need no plan rows. Returns and labels are add-ons: every existing plan gets them, so dress shops already on a
plan keep what they have today.

Revision ID: c3e8a1f5d927
Revises: b7d4f2a96c13
Create Date: 2026-11-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3e8a1f5d927'
down_revision: Union[str, None] = 'b7d4f2a96c13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_KEYS = "'pos', 'analytics', 'kds', 'inventory', 'offline_billing'"
NEW_KEYS = OLD_KEYS + ", 'apparel', 'apparel_returns', 'apparel_labels', 'hardware'"
REMOVED = "('apparel', 'apparel_returns', 'apparel_labels', 'hardware')"
MODULE_CHECKS = (
    ('plan_modules', 'ck_plan_modules_module_key'),
    ('organization_modules', 'ck_organization_modules_module_key'),
    ('platform_modules', 'ck_platform_modules_module_key'),
)


def _recreate(keys: str) -> None:
    for table, name in MODULE_CHECKS:
        op.drop_constraint(name, table, type_='check')
        op.create_check_constraint(name, table, f"module_key IN ({keys})")


def upgrade() -> None:
    _recreate(NEW_KEYS)
    op.execute(
        "INSERT INTO plan_modules (plan_id, module_key) "
        "SELECT p.id, k.key FROM plans p CROSS JOIN (VALUES ('apparel_returns'), ('apparel_labels')) AS k(key) "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    for table, _ in MODULE_CHECKS:
        op.execute(f"DELETE FROM {table} WHERE module_key IN {REMOVED}")
    _recreate(OLD_KEYS)
