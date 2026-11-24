"""hardware shops, stage H2: stock per product and branch, deliveries, and the stock ledger

New tables only (`hardware_stock`, `hardware_purchases`, `hardware_stock_movements`), each scoped to its branch with the
`tenant_scope` row-level security policy. Quantities are in the product's base unit (3 decimals). No existing table is touched.

Revision ID: b7d4f2a96c13
Revises: a9c3e5f71b48
Create Date: 2026-11-19 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7d4f2a96c13'
down_revision: Union[str, None] = 'a9c3e5f71b48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BRANCH_SCOPE = 'app_org_scope() IS NULL OR branch_id IN (SELECT id FROM branches WHERE organization_id = app_org_scope())'
TABLES = ('hardware_stock', 'hardware_purchases', 'hardware_stock_movements')
KINDS = "'purchase', 'sale', 'sale_void', 'return', 'adjustment', 'count'"


def upgrade() -> None:
    op.create_table(
        'hardware_stock',
        sa.Column('branch_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('product_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('quantity', sa.Numeric(14, 3), server_default='0', nullable=False),
        sa.Column('low_stock_level', sa.Numeric(14, 3), nullable=True),
        sa.Column('cost_price', sa.Numeric(12, 4), nullable=True),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('low_stock_level IS NULL OR low_stock_level >= 0', name='ck_hardware_stock_low_stock_level'),
        sa.CheckConstraint('cost_price IS NULL OR cost_price >= 0', name='ck_hardware_stock_cost_price'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['product_id'], ['hardware_products.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('branch_id', 'product_id', name='uq_hardware_stock_branch_product'),
    )
    op.create_index(op.f('ix_hardware_stock_branch_id'), 'hardware_stock', ['branch_id'])
    op.create_index(op.f('ix_hardware_stock_product_id'), 'hardware_stock', ['product_id'])

    op.create_table(
        'hardware_purchases',
        sa.Column('branch_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('supplier_name', sa.String(length=150), nullable=True),
        sa.Column('invoice_number', sa.String(length=50), nullable=True),
        sa.Column('received_on', sa.Date(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('total_cost', sa.Numeric(12, 2), server_default='0', nullable=False),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('total_cost >= 0', name='ck_hardware_purchases_total_cost'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_hardware_purchases_branch_id'), 'hardware_purchases', ['branch_id'])

    op.create_table(
        'hardware_stock_movements',
        sa.Column('branch_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('product_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('quantity', sa.Numeric(14, 3), nullable=False),
        sa.Column('balance_after', sa.Numeric(14, 3), nullable=False),
        sa.Column('unit_cost', sa.Numeric(12, 4), nullable=True),
        sa.Column('entered_unit', sa.String(length=10), nullable=True),
        sa.Column('entered_quantity', sa.Numeric(14, 3), nullable=True),
        sa.Column('entered_unit_cost', sa.Numeric(12, 4), nullable=True),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('purchase_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(f'kind IN ({KINDS})', name='ck_hardware_stock_movements_kind'),
        sa.CheckConstraint('quantity <> 0', name='ck_hardware_stock_movements_quantity'),
        sa.CheckConstraint('unit_cost IS NULL OR unit_cost >= 0', name='ck_hardware_stock_movements_unit_cost'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['product_id'], ['hardware_products.id']),
        sa.ForeignKeyConstraint(['purchase_id'], ['hardware_purchases.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_hardware_stock_movements_branch_product_created', 'hardware_stock_movements',
                    ['branch_id', 'product_id', 'created_at'])
    op.create_index(op.f('ix_hardware_stock_movements_purchase_id'), 'hardware_stock_movements', ['purchase_id'])

    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_scope ON "{table}" USING ({BRANCH_SCOPE}) WITH CHECK ({BRANCH_SCOPE})')


def downgrade() -> None:
    for table in ('hardware_stock_movements', 'hardware_purchases', 'hardware_stock'):
        op.execute(f'DROP POLICY IF EXISTS tenant_scope ON "{table}"')
        op.drop_table(table)
