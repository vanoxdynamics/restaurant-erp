from typing import AsyncGenerator

import uuid

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Optionally connect, then SET ROLE to a non-superuser role: row-level security does not apply to superusers.
_connect_args = {"server_settings": {"role": settings.DB_SET_ROLE}} if settings.DB_SET_ROLE else {}

engine = (
    create_async_engine(settings.DATABASE_URL, echo=settings.DB_ECHO, poolclass=NullPool, connect_args=_connect_args)
    if settings.DB_NULL_POOL
    else create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DB_ECHO,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        connect_args=_connect_args,
    )
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for obtaining an asynchronous SQLAlchemy database session.
    Services commit explicitly; this only guarantees rollback on error.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    await engine.dispose()


# --- tenant scoping for row-level security --------------------------------------------------------------------------------
#
# Every tenant table has a policy: rows are visible only when `app.org_scope` is unset (platform, login, workers) or equals
# the row's organization. Authenticated tenant requests set it, so even an application bug that forgets a WHERE clause
# cannot read or write another organization's rows. The setting is transaction-local (set_config(..., true)), so it can
# never leak to the next request that reuses a pooled connection.

_SCOPE_KEY = "org_scope"
_SET_SCOPE = text("SELECT set_config('app.org_scope', :org, true)")


@event.listens_for(Session, "after_begin")
def _apply_scope_to_new_transaction(session: Session, transaction, connection) -> None:
    org = session.info.get(_SCOPE_KEY)
    if org:
        connection.execute(_SET_SCOPE, {"org": org})


async def scope_session(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Restrict this session, from now on and in every later transaction, to one organization's rows."""
    org = str(org_id)
    db.info[_SCOPE_KEY] = org
    await db.execute(_SET_SCOPE, {"org": org})  # the transaction that is already open



async def rls_status(db: AsyncSession) -> str:
    """'enforced' when this connection's role is bound by row-level security, otherwise why it is not."""
    row = (await db.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))).first()
    if row is None:
        return "unknown"
    if row.rolsuper:
        return "bypassed: the database role is a superuser"
    if row.rolbypassrls:
        return "bypassed: the database role has BYPASSRLS"
    return "enforced"
