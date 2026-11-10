"""business branding: name and logo on bills

Organizations get a display name and a small logo, both set by the business itself and printed at the top of its bills.

Revision ID: d4a8e6b21c73
Revises: c2f7a9d14e58
Create Date: 2026-11-09 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4a8e6b21c73'
down_revision: Union[str, None] = 'c2f7a9d14e58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('organizations', sa.Column('display_name', sa.String(length=150), nullable=True))
    op.add_column('organizations', sa.Column('logo', sa.LargeBinary(), nullable=True))
    op.add_column('organizations', sa.Column('logo_content_type', sa.String(length=20), nullable=True))
    op.add_column('organizations', sa.Column('logo_updated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for column in ('logo_updated_at', 'logo_content_type', 'logo', 'display_name'):
        op.drop_column('organizations', column)
