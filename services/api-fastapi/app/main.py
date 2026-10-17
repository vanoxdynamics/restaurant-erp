from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.redis import close_redis_pool, get_redis_pool
from app.modules.catalog import router as catalog_router
from app.modules.floor import router as floor_router
from app.modules.orders import router as orders_router
from app.modules.kots import router as kots_router
from app.modules.billing import router as billing_router
from app.modules.staff import router as staff_router
from app.modules.analytics import router as analytics_router
from app.realtime import router as realtime_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan event handler for startup warmup and graceful shutdown."""
    # Startup: Initialize Redis pool
    get_redis_pool()
    yield
    # Shutdown: Close connections
    await close_redis_pool()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    description="High-performance asynchronous backend service for enterprise restaurant POS and Admin ERP",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS Configuration
if settings.ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.ALLOWED_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint for container orchestrators and load balancers."""
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "version": "0.1.0",
    }


# Mount API v1 Routers
app.include_router(catalog_router, prefix=settings.API_V1_STR)
app.include_router(floor_router, prefix=settings.API_V1_STR)
app.include_router(orders_router, prefix=settings.API_V1_STR)
app.include_router(kots_router, prefix=settings.API_V1_STR)
app.include_router(billing_router, prefix=settings.API_V1_STR)
app.include_router(staff_router, prefix=settings.API_V1_STR)
app.include_router(analytics_router, prefix=settings.API_V1_STR)

# Mount Realtime WebSocket Router
app.include_router(realtime_router)
