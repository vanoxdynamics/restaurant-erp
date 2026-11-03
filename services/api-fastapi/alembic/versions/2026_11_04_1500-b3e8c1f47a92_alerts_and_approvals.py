"""platform alerts and four-eyes approvals

Revision ID: b3e8c1f47a92
Revises: a9d4f7c13e60
Create Date: 2026-11-04 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3e8c1f47a92'
down_revision: Union[str, None] = 'a9d4f7c13e60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_alerts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(length=60), nullable=False),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('message', sa.String(length=500), nullable=False),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("severity IN ('info', 'warning', 'critical')", name='ck_platform_alerts_severity'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_platform_alerts_kind'), 'platform_alerts', ['kind'])
    op.create_index('ix_platform_alerts_open', 'platform_alerts', ['created_at'], postgresql_where=sa.text('acknowledged_at IS NULL'))

    op.create_table(
        'platform_approvals',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('action', sa.String(length=60), nullable=False),
        sa.Column('summary', sa.String(length=300), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('requested_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decision_note', sa.String(length=500), nullable=True),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'executed', 'rejected', 'cancelled', 'expired', 'failed')", name='ck_platform_approvals_status'),
        sa.ForeignKeyConstraint(['requested_by'], ['platform_admins.id']),
        sa.ForeignKeyConstraint(['decided_by'], ['platform_admins.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_platform_approvals_action'), 'platform_approvals', ['action'])
    op.create_index(op.f('ix_platform_approvals_status'), 'platform_approvals', ['status'])


def downgrade() -> None:
    op.drop_table('platform_approvals')
    op.drop_table('platform_alerts')
