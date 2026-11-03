"""invoices, payments, billing notifications and account credit

Revision ID: a9d4f7c13e60
Revises: f1b6d3a82c47
Create Date: 2026-11-04 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a9d4f7c13e60'
down_revision: Union[str, None] = 'f1b6d3a82c47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('subscriptions', sa.Column('credit_balance', sa.Numeric(12, 2), server_default='0', nullable=False))
    op.create_check_constraint('ck_subscriptions_credit_balance', 'subscriptions', 'credit_balance >= 0')
    op.execute("CREATE SEQUENCE platform_invoice_seq START 1")

    op.create_table(
        'invoices',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('number', sa.String(length=30), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('subscription_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('billing_term', sa.String(length=20), nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('credit_applied', sa.Numeric(12, 2), server_default='0', nullable=False),
        sa.Column('amount_due', sa.Numeric(12, 2), nullable=False),
        sa.Column('amount_paid', sa.Numeric(12, 2), server_default='0', nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('void_reason', sa.String(length=500), nullable=True),
        sa.Column('created_by_admin', postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("kind IN ('renewal', 'activation', 'plan_change', 'manual')", name='ck_invoices_kind'),
        sa.CheckConstraint("status IN ('open', 'paid', 'void')", name='ck_invoices_status'),
        sa.CheckConstraint("billing_term IN ('monthly', 'yearly')", name='ck_invoices_billing_term'),
        sa.CheckConstraint("amount >= 0 AND credit_applied >= 0 AND amount_due >= 0 AND amount_paid >= 0", name='ck_invoices_amounts'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('number'),
    )
    op.create_index(op.f('ix_invoices_organization_id'), 'invoices', ['organization_id'])
    op.create_index(op.f('ix_invoices_subscription_id'), 'invoices', ['subscription_id'])
    op.create_index(op.f('ix_invoices_status'), 'invoices', ['status'])
    op.create_index(op.f('ix_invoices_due_at'), 'invoices', ['due_at'])
    op.create_index('uq_invoices_renewal_period', 'invoices', ['subscription_id', 'period_start'], unique=True,
                    postgresql_where=sa.text("kind = 'renewal' AND status <> 'void'"))

    op.create_table(
        'invoice_payments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('invoice_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=False),
        sa.Column('reference', sa.String(length=100), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('recorded_by_admin', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("method IN ('bank_transfer', 'upi', 'card_offline', 'cash', 'cheque', 'other')", name='ck_invoice_payments_method'),
        sa.CheckConstraint("amount > 0", name='ck_invoice_payments_amount'),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_invoice_payments_invoice_id'), 'invoice_payments', ['invoice_id'])
    op.create_index(op.f('ix_invoice_payments_organization_id'), 'invoice_payments', ['organization_id'])
    op.create_index('uq_invoice_payments_reference', 'invoice_payments', ['invoice_id', 'reference'], unique=True,
                    postgresql_where=sa.text('reference IS NOT NULL'))

    op.create_table(
        'billing_notifications',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('dedupe_key', sa.String(length=120), nullable=False),
        sa.Column('to_email', sa.String(length=254), nullable=True),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dedupe_key'),
    )
    op.create_index(op.f('ix_billing_notifications_organization_id'), 'billing_notifications', ['organization_id'])


def downgrade() -> None:
    op.drop_table('billing_notifications')
    op.drop_table('invoice_payments')
    op.drop_table('invoices')
    op.execute("DROP SEQUENCE IF EXISTS platform_invoice_seq")
    op.drop_constraint('ck_subscriptions_credit_balance', 'subscriptions', type_='check')
    op.drop_column('subscriptions', 'credit_balance')
