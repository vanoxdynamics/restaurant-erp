"""dress shops, stage A2: stock per variant and branch, deliveries, and the stock ledger

New tables only (`apparel_stock`, `apparel_purchases`, `apparel_stock_movements`), each scoped to its branch with the
`tenant_scope` row-level security policy. No existing table is touched.

Revision ID: d8e2a6c47f31
Revises: c3d5f8a14b27
Create Date: 2026-11-16 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd8e2a6c47f31'
down_revision: Union[str, None] = 'c3d5f8a14b27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BRANCH_SCOPE = 'app_org_scope() IS NULL OR branch_id IN (SELECT id FROM branches WHERE organization_id = app_org_scope())'
TABLES = ('apparel_stock', 'apparel_purchases', 'apparel_stock_movements')
KINDS = "'purchase', 'sale', 'sale_void', 'return', 'adjustment', 'count'"


def upgrade() -> None:
    op.create_table(
        'apparel_stock',
        sa.Column('branch_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('variant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('quantity', sa.Integer(), server_default='0', nullable=False),
        sa.Column('low_stock_level', sa.Integer(), nullable=True),
        sa.Column('cost_price', sa.Numeric(10, 2), nullable=True),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('low_stock_level IS NULL OR low_stock_level >= 0', name='ck_apparel_stock_low_stock_level'),
        sa.CheckConstraint('cost_price IS NULL OR cost_price >= 0', name='ck_apparel_stock_cost_price'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['variant_id'], ['apparel_variants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('branch_id', 'variant_id', name='uq_apparel_stock_branch_variant'),
    )
    op.create_index(op.f('ix_apparel_stock_branch_id'), 'apparel_stock', ['branch_id'])
    op.create_index(op.f('ix_apparel_stock_variant_id'), 'apparel_stock', ['variant_id'])

    op.create_table(
        'apparel_purchases',
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
        sa.CheckConstraint('total_cost >= 0', name='ck_apparel_purchases_total_cost'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_apparel_purchases_branch_id'), 'apparel_purchases', ['branch_id'])

    op.create_table(
        'apparel_stock_movements',
        sa.Column('branch_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('variant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('balance_after', sa.Integer(), nullable=False),
        sa.Column('unit_cost', sa.Numeric(10, 2), nullable=True),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('purchase_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(f'kind IN ({KINDS})', name='ck_apparel_stock_movements_kind'),
        sa.CheckConstraint('quantity <> 0', name='ck_apparel_stock_movements_quantity'),
        sa.CheckConstraint('unit_cost IS NULL OR unit_cost >= 0', name='ck_apparel_stock_movements_unit_cost'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['variant_id'], ['apparel_variants.id']),
        sa.ForeignKeyConstraint(['purchase_id'], ['apparel_purchases.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_apparel_stock_movements_branch_variant_created', 'apparel_stock_movements',
                    ['branch_id', 'variant_id', 'created_at'])
    op.create_index(op.f('ix_apparel_stock_movements_purchase_id'), 'apparel_stock_movements', ['purchase_id'])

    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_scope ON "{table}" USING ({BRANCH_SCOPE}) WITH CHECK ({BRANCH_SCOPE})')


def downgrade() -> None:
    for table in ('apparel_stock_movements', 'apparel_purchases', 'apparel_stock'):
        op.execute(f'DROP POLICY IF EXISTS tenant_scope ON "{table}"')
        op.drop_table(table)
