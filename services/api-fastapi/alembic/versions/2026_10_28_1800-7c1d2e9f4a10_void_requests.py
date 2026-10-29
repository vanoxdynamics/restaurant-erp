"""void requests

Remote void approval queue: floor staff request a KOT void, a manager approves or rejects it.

Revision ID: 7c1d2e9f4a10
Revises: 5aeda782303f
Create Date: 2026-10-28 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7c1d2e9f4a10'
down_revision: Union[str, None] = '5aeda782303f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'void_requests',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('branch_id', sa.UUID(), nullable=False),
        sa.Column('kot_id', sa.UUID(), nullable=False),
        sa.Column('order_id', sa.UUID(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('requested_by_id', sa.UUID(), nullable=False),
        sa.Column('reviewed_by_id', sa.UUID(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('review_note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], name='fk_void_requests_branch_id'),
        sa.ForeignKeyConstraint(['kot_id'], ['kots.id'], name='fk_void_requests_kot_id'),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name='fk_void_requests_order_id'),
        sa.ForeignKeyConstraint(['requested_by_id'], ['staff_users.id'], name='fk_void_requests_requested_by_id'),
        sa.ForeignKeyConstraint(['reviewed_by_id'], ['staff_users.id'], name='fk_void_requests_reviewed_by_id'),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name='ck_void_requests_status'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_void_requests_kot_id'), 'void_requests', ['kot_id'], unique=False)
    op.create_index('ix_void_requests_branch_status', 'void_requests', ['branch_id', 'status'], unique=False)
    op.create_index(
        'uq_void_requests_kot_pending', 'void_requests', ['kot_id'], unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index('uq_void_requests_kot_pending', table_name='void_requests')
    op.drop_index('ix_void_requests_branch_status', table_name='void_requests')
    op.drop_index(op.f('ix_void_requests_kot_id'), table_name='void_requests')
    op.drop_table('void_requests')
