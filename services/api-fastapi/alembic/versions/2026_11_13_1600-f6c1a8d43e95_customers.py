"""customers: who a business sells to, and the customer on an order

Adds the business-scoped `customers` table (with the same row-level security policy as every other table keyed by organization)
and `orders.customer_id`.

Revision ID: f6c1a8d43e95
Revises: e5b9f7c32d84
Create Date: 2026-11-13 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f6c1a8d43e95'
down_revision: Union[str, None] = 'e5b9f7c32d84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCOPE = 'app_org_scope() IS NULL OR organization_id = app_org_scope()'


def upgrade() -> None:
    op.create_table(
        'customers',
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('phone', sa.String(length=15), nullable=True),
        sa.Column('email', sa.String(length=254), nullable=True),
        sa.Column('gstin', sa.String(length=15), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_customers_organization_id'), 'customers', ['organization_id'], unique=False)
    op.create_index('ix_customers_org_name', 'customers', ['organization_id', 'name'], unique=False)
    op.create_index('uq_customers_org_phone', 'customers', ['organization_id', 'phone'], unique=True,
                    postgresql_where=sa.text('phone IS NOT NULL'))
    op.execute('ALTER TABLE "customers" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "customers" FORCE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY tenant_scope ON "customers" USING ({SCOPE}) WITH CHECK ({SCOPE})')

    op.add_column('orders', sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(op.f('ix_orders_customer_id'), 'orders', ['customer_id'], unique=False)
    op.create_foreign_key('fk_orders_customer_id_customers', 'orders', 'customers', ['customer_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_orders_customer_id_customers', 'orders', type_='foreignkey')
    op.drop_index(op.f('ix_orders_customer_id'), table_name='orders')
    op.drop_column('orders', 'customer_id')
    op.drop_table('customers')
