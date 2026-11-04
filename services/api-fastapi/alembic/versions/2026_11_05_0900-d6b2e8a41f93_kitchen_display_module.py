"""kitchen display module and ticket fulfilment

Adds the `kds` module key, and `kots.fulfilment` ('screen' or 'slip'). Every existing plan that includes the point of sale
also gets `kds`, so nothing changes for current subscribers: today everyone has the kitchen screen.

Revision ID: d6b2e8a41f93
Revises: c4f9a2d85e17
Create Date: 2026-11-05 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd6b2e8a41f93'
down_revision: Union[str, None] = 'c4f9a2d85e17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_KEYS = "'pos', 'analytics'"
NEW_KEYS = "'pos', 'analytics', 'kds'"
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
    op.add_column('kots', sa.Column('fulfilment', sa.String(length=10), server_default='screen', nullable=False))
    op.create_check_constraint('ck_kots_fulfilment', 'kots', "fulfilment IN ('screen', 'slip')")

    _recreate(NEW_KEYS)
    # Behaviour-preserving: every plan that has the POS keeps the kitchen screen it has today.
    op.execute("""
        INSERT INTO plan_modules (plan_id, module_key)
        SELECT pm.plan_id, 'kds' FROM plan_modules pm
        WHERE pm.module_key = 'pos'
          AND NOT EXISTS (SELECT 1 FROM plan_modules x WHERE x.plan_id = pm.plan_id AND x.module_key = 'kds')
    """)


def downgrade() -> None:
    op.execute("DELETE FROM plan_modules WHERE module_key = 'kds'")
    op.execute("DELETE FROM organization_modules WHERE module_key = 'kds'")
    op.execute("DELETE FROM platform_modules WHERE module_key = 'kds'")
    _recreate(OLD_KEYS)
    op.drop_constraint('ck_kots_fulfilment', 'kots', type_='check')
    op.drop_column('kots', 'fulfilment')
