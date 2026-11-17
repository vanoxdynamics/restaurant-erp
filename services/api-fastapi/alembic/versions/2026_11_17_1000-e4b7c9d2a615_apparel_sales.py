"""dress shops, stage A3: sales, sale lines and payments

New tables `apparel_sales`, `apparel_sale_lines`, `apparel_payments` (each scoped to its branch with the `tenant_scope`
row-level security policy) and a `sale_id` on the stock ledger. No existing table is otherwise touched.

Revision ID: e4b7c9d2a615
Revises: d8e2a6c47f31
Create Date: 2026-11-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e4b7c9d2a615'
down_revision: Union[str, None] = 'd8e2a6c47f31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BRANCH_SCOPE = 'app_org_scope() IS NULL OR branch_id IN (SELECT id FROM branches WHERE organization_id = app_org_scope())'
TABLES = ('apparel_sales', 'apparel_sale_lines', 'apparel_payments')


def _id_and_times() -> list:
    return [
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def _money(name: str, nullable: bool = False, default: bool = False) -> sa.Column:
    return sa.Column(name, sa.Numeric(10, 2), nullable=nullable, server_default='0' if default else None)


def upgrade() -> None:
    uuid_fk = lambda name, nullable=True: sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)  # noqa: E731

    op.create_table(
        'apparel_sales',
        uuid_fk('branch_id', False),
        sa.Column('sale_number', sa.String(length=50), nullable=False),
        sa.Column('business_date', sa.Date(), nullable=False),
        uuid_fk('customer_id'), uuid_fk('salesperson_id'), uuid_fk('created_by_id'),
        _money('subtotal'), _money('discount_amount', default=True),
        sa.Column('discount_reason', sa.Text(), nullable=True),
        uuid_fk('discount_approved_by_id'),
        _money('taxable_value'), _money('tax_cgst'), _money('tax_sgst'), _money('tax_igst', default=True),
        _money('round_off', default=True), _money('grand_total'), _money('paid_amount', default=True),
        sa.Column('status', sa.String(length=30), server_default='unpaid', nullable=False),
        sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
        uuid_fk('voided_by_id'),
        sa.Column('void_reason', sa.Text(), nullable=True),
        sa.Column('client_sale_id', sa.String(length=64), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        *_id_and_times(),
        sa.CheckConstraint("status IN ('unpaid', 'partially_paid', 'paid', 'voided')", name='ck_apparel_sales_status'),
        sa.CheckConstraint('grand_total >= 0 AND paid_amount >= 0', name='ck_apparel_sales_amounts'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id']),
        sa.ForeignKeyConstraint(['salesperson_id'], ['staff_users.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['staff_users.id']),
        sa.ForeignKeyConstraint(['discount_approved_by_id'], ['staff_users.id']),
        sa.ForeignKeyConstraint(['voided_by_id'], ['staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('branch_id', 'sale_number', name='uq_apparel_sales_branch_number'),
    )
    op.create_index(op.f('ix_apparel_sales_branch_id'), 'apparel_sales', ['branch_id'])
    op.create_index(op.f('ix_apparel_sales_business_date'), 'apparel_sales', ['business_date'])
    op.create_index(op.f('ix_apparel_sales_customer_id'), 'apparel_sales', ['customer_id'])
    op.create_index('uq_apparel_sales_branch_client_sale', 'apparel_sales', ['branch_id', 'client_sale_id'], unique=True,
                    postgresql_where=sa.text('client_sale_id IS NOT NULL'))

    op.create_table(
        'apparel_sale_lines',
        uuid_fk('branch_id', False), uuid_fk('sale_id', False), uuid_fk('variant_id', False),
        sa.Column('name_snapshot', sa.String(length=150), nullable=False),
        sa.Column('size_snapshot', sa.String(length=20), nullable=False),
        sa.Column('colour_snapshot', sa.String(length=40), nullable=False),
        sa.Column('hsn_snapshot', sa.String(length=8), nullable=True),
        sa.Column('barcode_snapshot', sa.String(length=64), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        _money('mrp_snapshot', nullable=True), _money('unit_price'), _money('line_discount', default=True),
        _money('discount', default=True),
        sa.Column('tax_rate', sa.Numeric(5, 2), nullable=False),
        _money('taxable_value'), _money('cgst'), _money('sgst'), _money('igst', default=True), _money('line_total'),
        sa.Column('returned_quantity', sa.Integer(), server_default='0', nullable=False),
        *_id_and_times(),
        sa.CheckConstraint('quantity > 0', name='ck_apparel_sale_lines_quantity'),
        sa.CheckConstraint('returned_quantity >= 0 AND returned_quantity <= quantity', name='ck_apparel_sale_lines_returned'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['sale_id'], ['apparel_sales.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['variant_id'], ['apparel_variants.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_apparel_sale_lines_branch_id'), 'apparel_sale_lines', ['branch_id'])
    op.create_index(op.f('ix_apparel_sale_lines_sale_id'), 'apparel_sale_lines', ['sale_id'])
    op.create_index(op.f('ix_apparel_sale_lines_variant_id'), 'apparel_sale_lines', ['variant_id'])

    op.create_table(
        'apparel_payments',
        sa.Column('payment_mode', sa.String(length=30), nullable=False),
        _money('amount'),
        sa.Column('reference_id', sa.String(length=100), nullable=True),
        sa.Column('status', sa.String(length=30), server_default='success', nullable=False),
        sa.Column('idempotency_key', sa.String(length=100), nullable=True),
        sa.Column('provider', sa.String(length=30), server_default='manual', nullable=False),
        sa.Column('provider_meta', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        uuid_fk('received_by_id'), uuid_fk('shift_id'),
        uuid_fk('branch_id', False), uuid_fk('sale_id', False),
        *_id_and_times(),
        sa.CheckConstraint("payment_mode IN ('cash', 'card', 'upi')", name='ck_apparel_payments_payment_mode'),
        sa.CheckConstraint('amount > 0', name='ck_apparel_payments_amount'),
        sa.ForeignKeyConstraint(['received_by_id'], ['staff_users.id']),
        sa.ForeignKeyConstraint(['shift_id'], ['shifts.id']),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['sale_id'], ['apparel_sales.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_apparel_payments_shift_id'), 'apparel_payments', ['shift_id'])
    op.create_index(op.f('ix_apparel_payments_branch_id'), 'apparel_payments', ['branch_id'])
    op.create_index(op.f('ix_apparel_payments_sale_id'), 'apparel_payments', ['sale_id'])
    op.create_index('uq_apparel_payments_sale_idempotency', 'apparel_payments', ['sale_id', 'idempotency_key'], unique=True,
                    postgresql_where=sa.text('idempotency_key IS NOT NULL'))

    op.add_column('apparel_stock_movements', sa.Column('sale_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_apparel_stock_movements_sale_id', 'apparel_stock_movements', 'apparel_sales', ['sale_id'], ['id'])
    op.create_index(op.f('ix_apparel_stock_movements_sale_id'), 'apparel_stock_movements', ['sale_id'])

    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_scope ON "{table}" USING ({BRANCH_SCOPE}) WITH CHECK ({BRANCH_SCOPE})')


def downgrade() -> None:
    op.drop_index(op.f('ix_apparel_stock_movements_sale_id'), table_name='apparel_stock_movements')
    op.drop_constraint('fk_apparel_stock_movements_sale_id', 'apparel_stock_movements', type_='foreignkey')
    op.drop_column('apparel_stock_movements', 'sale_id')
    for table in ('apparel_payments', 'apparel_sale_lines', 'apparel_sales'):
        op.execute(f'DROP POLICY IF EXISTS tenant_scope ON "{table}"')
        op.drop_table(table)
