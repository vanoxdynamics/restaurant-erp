"""platform admin accounts, sessions and audit log

Revision ID: e8a3c5d71b29
Revises: d7f2b9c40a18
Create Date: 2026-11-03 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e8a3c5d71b29'
down_revision: Union[str, None] = 'd7f2b9c40a18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLES = "'super_admin', 'billing', 'support', 'read_only'"


def _ts(name: str, **kw) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, **kw)


def upgrade() -> None:
    op.create_table(
        'platform_admins',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('full_name', sa.String(length=100), nullable=False),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('totp_secret_enc', sa.String(length=255), nullable=False),
        sa.Column('totp_last_step', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('role', sa.String(length=30), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('token_version', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        _ts('password_changed_at'), _ts('created_at'), _ts('updated_at'),
        sa.CheckConstraint(f"role IN ({ROLES})", name='ck_platform_admins_role'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_platform_admins_email'), 'platform_admins', ['email'], unique=True)

    op.create_table(
        'platform_recovery_codes',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('admin_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['admin_id'], ['platform_admins.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_platform_recovery_codes_admin_id'), 'platform_recovery_codes', ['admin_id'])

    op.create_table(
        'platform_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('admin_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('refresh_hash', sa.String(length=64), nullable=False),
        sa.Column('prev_refresh_hash', sa.String(length=64), nullable=True),
        _ts('created_at'), _ts('last_seen_at'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_reason', sa.String(length=40), nullable=True),
        sa.Column('step_up_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['admin_id'], ['platform_admins.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('refresh_hash'),
    )
    op.create_index(op.f('ix_platform_sessions_admin_id'), 'platform_sessions', ['admin_id'])
    op.create_index(op.f('ix_platform_sessions_prev_refresh_hash'), 'platform_sessions', ['prev_refresh_hash'])

    op.create_table(
        'platform_audit_logs',
        sa.Column('seq', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('admin_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('action', sa.String(length=60), nullable=False),
        sa.Column('target_type', sa.String(length=40), nullable=True),
        sa.Column('target_id', sa.String(length=64), nullable=True),
        sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('prev_hash', sa.String(length=64), nullable=False),
        sa.Column('hash', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('seq'),
    )
    op.create_index(op.f('ix_platform_audit_logs_admin_id'), 'platform_audit_logs', ['admin_id'])
    op.create_index(op.f('ix_platform_audit_logs_action'), 'platform_audit_logs', ['action'])
    op.create_index('ix_platform_audit_logs_target', 'platform_audit_logs', ['target_type', 'target_id'])

    # Append-only: even the application role cannot rewrite history.
    op.execute("""
        CREATE FUNCTION platform_audit_logs_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'platform_audit_logs is append-only';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER platform_audit_logs_no_change
        BEFORE UPDATE OR DELETE ON platform_audit_logs
        FOR EACH ROW EXECUTE FUNCTION platform_audit_logs_immutable();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS platform_audit_logs_no_change ON platform_audit_logs")
    op.execute("DROP FUNCTION IF EXISTS platform_audit_logs_immutable()")
    op.drop_table('platform_audit_logs')
    op.drop_table('platform_sessions')
    op.drop_table('platform_recovery_codes')
    op.drop_table('platform_admins')
