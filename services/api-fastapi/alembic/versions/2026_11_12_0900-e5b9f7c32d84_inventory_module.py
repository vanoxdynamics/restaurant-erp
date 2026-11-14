"""inventory module: stock levels, purchases and the stock ledger

Adds the `inventory` module key and three branch-scoped tables with the same row-level security policy as every other tenant
table. Inventory is a paid add-on, so existing plans do not get it; only the internal all-modules legacy plan does.

Revision ID: e5b9f7c32d84
Revises: d4a8e6b21c73
Create Date: 2026-11-12 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e5b9f7c32d84'
down_revision: Union[str, None] = 'd4a8e6b21c73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_KEYS = "'pos', 'analytics', 'kds'"
NEW_KEYS = "'pos', 'analytics', 'kds', 'inventory'"
MODULE_CHECKS = (
    ('plan_modules', 'ck_plan_modules_module_key'),
    ('organization_modules', 'ck_organization_modules_module_key'),
    ('platform_modules', 'ck_platform_modules_module_key'),
)
TABLES = ('stock_levels', 'stock_purchases', 'stock_movements')
SCOPE = 'app_org_scope() IS NULL OR branch_id IN (SELECT id FROM branches WHERE organization_id = app_org_scope())'


def _recreate(keys: str) -> None:
    for table, name in MODULE_CHECKS:
        op.drop_constraint(name, table, type_='check')
        op.create_check_constraint(name, table, f"module_key IN ({keys})")


def _timestamps() -> list:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        'stock_levels',
        sa.Column('branch_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('item_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=12, scale=3), server_default='0', nullable=False),
        sa.Column('low_stock_level', sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column('cost_price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('is_tracked', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint('cost_price IS NULL OR cost_price >= 0', name='ck_stock_levels_cost_price'),
        sa.CheckConstraint('low_stock_level IS NULL OR low_stock_level >= 0', name='ck_stock_levels_low_stock_level'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['item_id'], ['menu_items.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('branch_id', 'item_id', name='uq_stock_levels_branch_item'),
    )
    op.create_index(op.f('ix_stock_levels_branch_id'), 'stock_levels', ['branch_id'], unique=False)
    op.create_index(op.f('ix_stock_levels_item_id'), 'stock_levels', ['item_id'], unique=False)

    op.create_table(
        'stock_purchases',
        sa.Column('branch_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('supplier_name', sa.String(length=150), nullable=True),
        sa.Column('invoice_number', sa.String(length=50), nullable=True),
        sa.Column('received_on', sa.Date(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('total_cost', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint('total_cost >= 0', name='ck_stock_purchases_total_cost'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_stock_purchases_branch_id'), 'stock_purchases', ['branch_id'], unique=False)

    op.create_table(
        'stock_movements',
        sa.Column('branch_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('item_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('balance_after', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('unit_cost', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('purchase_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('order_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint("kind IN ('purchase', 'sale', 'adjustment', 'count')", name='ck_stock_movements_kind'),
        sa.CheckConstraint('quantity <> 0', name='ck_stock_movements_quantity'),
        sa.CheckConstraint('unit_cost IS NULL OR unit_cost >= 0', name='ck_stock_movements_unit_cost'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['staff_users.id']),
        sa.ForeignKeyConstraint(['item_id'], ['menu_items.id']),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id']),
        sa.ForeignKeyConstraint(['purchase_id'], ['stock_purchases.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_stock_movements_branch_item_created', 'stock_movements', ['branch_id', 'item_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_stock_movements_order_id'), 'stock_movements', ['order_id'], unique=False)
    op.create_index(op.f('ix_stock_movements_purchase_id'), 'stock_movements', ['purchase_id'], unique=False)

    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_scope ON "{table}" USING ({SCOPE}) WITH CHECK ({SCOPE})')

    _recreate(NEW_KEYS)
    op.execute("""
        INSERT INTO plan_modules (plan_id, module_key)
        SELECT p.id, 'inventory' FROM plans p
        WHERE p.code = 'legacy_all_modules'
          AND NOT EXISTS (SELECT 1 FROM plan_modules x WHERE x.plan_id = p.id AND x.module_key = 'inventory')
    """)


def downgrade() -> None:
    op.execute("DELETE FROM plan_modules WHERE module_key = 'inventory'")
    op.execute("DELETE FROM organization_modules WHERE module_key = 'inventory'")
    op.execute("DELETE FROM platform_modules WHERE module_key = 'inventory'")
    _recreate(OLD_KEYS)
    for table in reversed(TABLES):
        op.drop_table(table)
