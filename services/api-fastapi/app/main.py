import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal, dispose_engine, engine, rls_status
from app.core.errors import register_exception_handlers
from app.core.events import set_publisher
from app.core.logging import RequestIdMiddleware, configure_logging
from app.core.platform_middleware import PlatformGuardMiddleware
from app.core.redis import close_redis_pool, redis_client
from app.modules.analytics import router as analytics_router
from app.modules.approvals import router as approvals_router
from app.modules.billing import router as billing_router
from app.modules.business.router import router as business_router
from app.modules.catalog import router as catalog_router
from app.modules.customers.router import router as customers_router
from app.modules.devices.router import router as devices_router
from app.modules.entitlements import router as entitlements_router
from app.modules.floor import router as floor_router
from app.modules.inventory.router import router as inventory_router
from app.modules.kots import router as kots_router
from app.modules.kots.station import router as kots_station_router
from app.modules.notices.router import router as notices_router
from app.modules.orders import router as orders_router
from app.modules.retail.router import router as retail_router
from app.modules.platform.billing_router import router as platform_billing_router
from app.modules.platform.orgs_router import router as platform_orgs_router
from app.modules.platform.router import auth_router as platform_auth_router
from app.modules.platform.security_router import router as platform_security_router
from app.modules.platform.router import router as platform_router
from app.modules.staff import router as staff_router
from app.realtime import realtime_manager
from app.realtime import router as realtime_router

configure_logging(settings.DEBUG)
logger = logging.getLogger(__name__)


async def check_dependencies() -> dict:
    status = {"database": "ok", "redis": "ok"}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        status["database"] = f"error: {type(exc).__name__}"
    client = redis_client()
    try:
        await client.ping()
    except Exception as exc:
        status["redis"] = f"error: {type(exc).__name__}"
    finally:
        await client.aclose()
    return status


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Verify dependencies, start the realtime subscriber, and shut everything down cleanly."""
    status = await check_dependencies()
    if any(v != "ok" for v in status.values()):
        logger.error("Startup dependency check failed: %s", status)
        raise RuntimeError(f"Dependencies unavailable: {status}")
    async with AsyncSessionLocal() as db:
        rls = await rls_status(db)
    if rls != "enforced":
        # Row-level security is a backstop, not the only defence; but in production it should be real.
        (logger.error if settings.is_production else logger.warning)(
            "Row-level security is NOT enforced (%s). Connect as a non-superuser role or set DB_SET_ROLE.", rls)
    set_publisher(realtime_manager.publish)
    realtime_manager.start()
    yield
    await realtime_manager.stop()
    set_publisher(None)
    await close_redis_pool()
    await dispose_engine()


docs_enabled = not settings.is_production

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.2.0",
    description="High-performance asynchronous backend service for enterprise restaurant POS and Admin ERP",
    openapi_url="/openapi.json" if docs_enabled else None,
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    lifespan=lifespan,
)

register_exception_handlers(app)
app.add_middleware(PlatformGuardMiddleware)
app.add_middleware(RequestIdMiddleware)

# CORS Configuration
if settings.ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag", "X-Request-ID", "Retry-After", "Idempotent-Replay"],
    )


@app.get("/health", tags=["Health"])
async def health_check():
    """Liveness probe: the process is up (no I/O)."""
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "version": app.version,
    }


@app.get("/health/ready", tags=["Health"])
async def readiness_check():
    """Readiness probe: database and Redis reachable."""
    status = await check_dependencies()
    ready = all(v == "ok" for v in status.values())
    async with AsyncSessionLocal() as db:
        rls = await rls_status(db)
    body = {"status": "ready" if ready else "unavailable", **status, "realtime": realtime_manager.stats(), "rls": rls}
    return JSONResponse(body, status_code=200 if ready else 503)


# Mount API v1 Routers
app.include_router(catalog_router, prefix=settings.API_V1_STR)
app.include_router(retail_router, prefix=settings.API_V1_STR)
app.include_router(inventory_router, prefix=settings.API_V1_STR)
app.include_router(customers_router, prefix=settings.API_V1_STR)
app.include_router(entitlements_router, prefix=settings.API_V1_STR)
app.include_router(floor_router, prefix=settings.API_V1_STR)
app.include_router(orders_router, prefix=settings.API_V1_STR)
app.include_router(notices_router, prefix=settings.API_V1_STR)
app.include_router(kots_station_router, prefix=settings.API_V1_STR)
app.include_router(kots_router, prefix=settings.API_V1_STR)
app.include_router(billing_router, prefix=settings.API_V1_STR)
app.include_router(business_router, prefix=settings.API_V1_STR)
app.include_router(devices_router, prefix=settings.API_V1_STR)
app.include_router(staff_router, prefix=settings.API_V1_STR)
app.include_router(analytics_router, prefix=settings.API_V1_STR)
app.include_router(approvals_router, prefix=settings.API_V1_STR)
app.include_router(platform_auth_router, prefix=settings.API_V1_STR)
app.include_router(platform_router, prefix=settings.API_V1_STR)
app.include_router(platform_orgs_router, prefix=settings.API_V1_STR)
app.include_router(platform_billing_router, prefix=settings.API_V1_STR)
app.include_router(platform_security_router, prefix=settings.API_V1_STR)

# Mount Realtime WebSocket Router
app.include_router(realtime_router)
