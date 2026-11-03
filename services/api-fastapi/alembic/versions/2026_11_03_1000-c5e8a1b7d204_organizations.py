"""organizations

Adds the subscriber (tenant) layer above branches. Existing single-tenant data is attached to one
"default" organization so nothing changes for current deployments.

Revision ID: c5e8a1b7d204
Revises: a4c7d9e2b610
Create Date: 2026-11-03 10:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c5e8a1b7d204'
down_revision: Union[str, None] = 'a4c7d9e2b610'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCOPED_TABLES = ('branches', 'staff_users', 'categories', 'menu_items', 'modifier_groups')
STATUSES = "'trial', 'active', 'past_due', 'suspended', 'cancelled'"


def upgrade() -> None:
    op.create_table(
        'organizations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('slug', sa.String(length=60), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='active', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(f"status IN ({STATUSES})", name='ck_organizations_status'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_organizations_slug'), 'organizations', ['slug'], unique=True)

    for table in SCOPED_TABLES:
        op.add_column(table, sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=True))

    # Backfill: only create the default organization when there is legacy data to attach.
    bind = op.get_bind()
    has_data = any(
        bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {t})")).scalar() for t in SCOPED_TABLES
    )
    if has_data:
        default_id = uuid.uuid4()
        bind.execute(
            sa.text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Default Organization', 'default', 'active')"),
            {"id": default_id},
        )
        for table in SCOPED_TABLES:
            bind.execute(sa.text(f"UPDATE {table} SET organization_id = :id"), {"id": default_id})

    for table in SCOPED_TABLES:
        op.alter_column(table, 'organization_id', nullable=False)
        op.create_index(op.f(f'ix_{table}_organization_id'), table, ['organization_id'], unique=False)
        op.create_foreign_key(f'fk_{table}_organization_id', table, 'organizations', ['organization_id'], ['id'])


def downgrade() -> None:
    for table in reversed(SCOPED_TABLES):
        op.drop_constraint(f'fk_{table}_organization_id', table, type_='foreignkey')
        op.drop_index(op.f(f'ix_{table}_organization_id'), table_name=table)
        op.drop_column(table, 'organization_id')
    op.drop_index(op.f('ix_organizations_slug'), table_name='organizations')
    op.drop_table('organizations')
