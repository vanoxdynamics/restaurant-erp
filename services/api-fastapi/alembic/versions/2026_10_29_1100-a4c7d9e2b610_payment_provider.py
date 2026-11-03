"""payment provider

Records which channel captured a payment ("manual" today) so a card-terminal integration can be added
without another schema change.

Revision ID: a4c7d9e2b610
Revises: 9b3e5f1c2d77
Create Date: 2026-10-29 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a4c7d9e2b610'
down_revision: Union[str, None] = '9b3e5f1c2d77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('payments', sa.Column('provider', sa.String(length=30), server_default='manual', nullable=False))
    op.add_column('payments', sa.Column('provider_meta', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('payments', 'provider_meta')
    op.drop_column('payments', 'provider')
