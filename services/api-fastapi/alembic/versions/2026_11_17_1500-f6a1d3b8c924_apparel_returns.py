"""dress shops, stage A5: returns, credit notes and store credit

New tables `apparel_returns`, `apparel_return_lines` (branch scope) and `apparel_store_credits` (organization scope: store
credit is valid at every branch), a `return_id` on the stock ledger, and `credit_note` as a payment mode with the credit it spent.

Revision ID: f6a1d3b8c924
Revises: e4b7c9d2a615
Create Date: 2026-11-17 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f6a1d3b8c924'
down_revision: Union[str, None] = 'e4b7c9d2a615'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BRANCH_SCOPE = 'app_org_scope() IS NULL OR branch_id IN (SELECT id FROM branches WHERE organization_id = app_org_scope())'
ORG_SCOPE = 'app_org_scope() IS NULL OR organization_id = app_org_scope()'


def _id_and_times() -> list:
    return [
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def _money(name: str, default: bool = False) -> sa.Column:
    return sa.Column(name, sa.Numeric(10, 2), nullable=False, server_default='0' if default else None)


def _uuid(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        'apparel_returns',
        _uuid('branch_id', False),
        sa.Column('return_number', sa.String(length=50), nullable=False),
        _uuid('sale_id', False),
        sa.Column('business_date', sa.Date(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('refund_mode', sa.String(length=20), nullable=False),
        _money('total_amount'), _money('taxable_value'), _money('tax_cgst'), _money('tax_sgst'), _money('tax_igst', default=True),
        _uuid('created_by_id'), _uuid('approved_by_id'), _uuid('shift_id'),
        sa.Column('client_return_id', sa.String(length=64), nullable=True),
        *_id_and_times(),
        sa.CheckConstraint("refund_mode IN ('cash', 'store_credit')", name='ck_apparel_returns_refund_mode'),
        sa.CheckConstraint('total_amount >= 0', name='ck_apparel_returns_total'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['sale_id'], ['apparel_sales.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['staff_users.id']),
        sa.ForeignKeyConstraint(['approved_by_id'], ['staff_users.id']),
        sa.ForeignKeyConstraint(['shift_id'], ['shifts.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('branch_id', 'return_number', name='uq_apparel_returns_branch_number'),
    )
    for col in ('branch_id', 'sale_id', 'business_date', 'shift_id'):
        op.create_index(op.f(f'ix_apparel_returns_{col}'), 'apparel_returns', [col])
    op.create_index('uq_apparel_returns_branch_client_return', 'apparel_returns', ['branch_id', 'client_return_id'], unique=True,
                    postgresql_where=sa.text('client_return_id IS NOT NULL'))

    op.create_table(
        'apparel_return_lines',
        _uuid('branch_id', False), _uuid('return_id', False), _uuid('sale_line_id', False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        _money('amount'), _money('taxable_value'), _money('cgst'), _money('sgst'), _money('igst', default=True),
        sa.Column('restock', sa.Boolean(), server_default='true', nullable=False),
        *_id_and_times(),
        sa.CheckConstraint('quantity > 0', name='ck_apparel_return_lines_quantity'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['return_id'], ['apparel_returns.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sale_line_id'], ['apparel_sale_lines.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for col in ('branch_id', 'return_id', 'sale_line_id'):
        op.create_index(op.f(f'ix_apparel_return_lines_{col}'), 'apparel_return_lines', [col])

    op.create_table(
        'apparel_store_credits',
        _uuid('organization_id', False), _uuid('return_id', False),
        sa.Column('code', sa.String(length=50), nullable=False),
        _uuid('customer_id'),
        _money('amount'), _money('balance'),
        sa.Column('status', sa.String(length=20), server_default='open', nullable=False),
        *_id_and_times(),
        sa.CheckConstraint("status IN ('open', 'used', 'cancelled')", name='ck_apparel_store_credits_status'),
        sa.CheckConstraint('balance >= 0 AND balance <= amount', name='ck_apparel_store_credits_balance'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['return_id'], ['apparel_returns.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'code', name='uq_apparel_store_credits_org_code'),
        sa.UniqueConstraint('return_id', name='uq_apparel_store_credits_return'),
    )
    op.create_index(op.f('ix_apparel_store_credits_organization_id'), 'apparel_store_credits', ['organization_id'])

    # The ledger points at the return that put pieces back.
    op.add_column('apparel_stock_movements', _uuid('return_id'))
    op.create_foreign_key('fk_apparel_stock_movements_return_id', 'apparel_stock_movements', 'apparel_returns', ['return_id'], ['id'])
    op.create_index(op.f('ix_apparel_stock_movements_return_id'), 'apparel_stock_movements', ['return_id'])

    # Store credit as a way to pay, and which credit a payment spent.
    op.drop_constraint('ck_apparel_payments_payment_mode', 'apparel_payments', type_='check')
    op.create_check_constraint('ck_apparel_payments_payment_mode', 'apparel_payments',
                               "payment_mode IN ('cash', 'card', 'upi', 'credit_note')")
    op.add_column('apparel_payments', _uuid('credit_note_id'))
    op.create_foreign_key('fk_apparel_payments_credit_note_id', 'apparel_payments', 'apparel_store_credits', ['credit_note_id'], ['id'])
    op.create_index(op.f('ix_apparel_payments_credit_note_id'), 'apparel_payments', ['credit_note_id'])
    op.create_check_constraint('ck_apparel_payments_credit_note', 'apparel_payments',
                               "(credit_note_id IS NOT NULL) = (payment_mode = 'credit_note')")

    for table in ('apparel_returns', 'apparel_return_lines'):
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_scope ON "{table}" USING ({BRANCH_SCOPE}) WITH CHECK ({BRANCH_SCOPE})')
    op.execute('ALTER TABLE "apparel_store_credits" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "apparel_store_credits" FORCE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY tenant_scope ON "apparel_store_credits" USING ({ORG_SCOPE}) WITH CHECK ({ORG_SCOPE})')


def downgrade() -> None:
    op.drop_constraint('ck_apparel_payments_credit_note', 'apparel_payments', type_='check')
    op.drop_index(op.f('ix_apparel_payments_credit_note_id'), table_name='apparel_payments')
    op.drop_constraint('fk_apparel_payments_credit_note_id', 'apparel_payments', type_='foreignkey')
    op.drop_column('apparel_payments', 'credit_note_id')
    op.drop_constraint('ck_apparel_payments_payment_mode', 'apparel_payments', type_='check')
    op.create_check_constraint('ck_apparel_payments_payment_mode', 'apparel_payments', "payment_mode IN ('cash', 'card', 'upi')")
    op.drop_index(op.f('ix_apparel_stock_movements_return_id'), table_name='apparel_stock_movements')
    op.drop_constraint('fk_apparel_stock_movements_return_id', 'apparel_stock_movements', type_='foreignkey')
    op.drop_column('apparel_stock_movements', 'return_id')
    for table in ('apparel_store_credits', 'apparel_return_lines', 'apparel_returns'):
        op.execute(f'DROP POLICY IF EXISTS tenant_scope ON "{table}"')
        op.drop_table(table)
