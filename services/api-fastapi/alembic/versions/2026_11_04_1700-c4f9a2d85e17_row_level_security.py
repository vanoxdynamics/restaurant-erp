"""row-level security for tenant tables

A database-level backstop for tenant isolation. Authenticated tenant requests set the transaction-local setting
`app.org_scope`; a table's policy then shows only that organization's rows. When the setting is unset (platform API, login,
background workers) rows are not restricted. FORCE makes the policies bind the table owner too; superusers (and roles with
BYPASSRLS) always bypass, so run the API as an ordinary role (see DB_SET_ROLE).

Covered: every table with an `organization_id`, `organizations` itself, and every table with a `branch_id`. Child tables
without either column (order_items, kot_items, dining_tables, item_variants, ...) are only reachable through those parents.

Revision ID: c4f9a2d85e17
Revises: b3e8c1f47a92
Create Date: 2026-11-04 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4f9a2d85e17'
down_revision: Union[str, None] = 'b3e8c1f47a92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ORG_KEYED = (
    'branches', 'staff_users', 'categories', 'menu_items', 'modifier_groups', 'subscriptions', 'organization_modules',
    'invoices', 'invoice_payments', 'billing_notifications', 'subscription_events',
)
POLICY = 'tenant_scope'


def _enable(table: str, expr: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY {POLICY} ON "{table}" USING ({expr}) WITH CHECK ({expr})')


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION app_org_scope() RETURNS uuid
        LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('app.org_scope', true), '')::uuid $$
    """)
    for table in ORG_KEYED:
        _enable(table, 'app_org_scope() IS NULL OR organization_id = app_org_scope()')
    _enable('organizations', 'app_org_scope() IS NULL OR id = app_org_scope()')

    skip = ", ".join(f"'{t}'" for t in ORG_KEYED)
    op.execute(f"""
        DO $$
        DECLARE t text;
        BEGIN
            FOR t IN
                SELECT c.table_name FROM information_schema.columns c
                JOIN information_schema.tables i ON i.table_schema = c.table_schema AND i.table_name = c.table_name
                WHERE c.table_schema = 'public' AND c.column_name = 'branch_id' AND i.table_type = 'BASE TABLE'
                  AND c.table_name NOT IN ({skip})
            LOOP
                EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
                EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
                EXECUTE format(
                    'CREATE POLICY {POLICY} ON %I USING (app_org_scope() IS NULL OR branch_id IN '
                    '(SELECT id FROM branches WHERE organization_id = app_org_scope())) '
                    'WITH CHECK (app_org_scope() IS NULL OR branch_id IN '
                    '(SELECT id FROM branches WHERE organization_id = app_org_scope()))', t);
            END LOOP;
        END $$
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT schemaname, tablename FROM pg_policies WHERE schemaname = 'public' AND policyname = '{POLICY}'
            LOOP
                EXECUTE format('DROP POLICY {POLICY} ON %I.%I', r.schemaname, r.tablename);
                EXECUTE format('ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY', r.schemaname, r.tablename);
                EXECUTE format('ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY', r.schemaname, r.tablename);
            END LOOP;
        END $$
    """)
    op.execute("DROP FUNCTION IF EXISTS app_org_scope()")
