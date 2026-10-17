# Restaurant ERP Backend API (`services/api-fastapi`)

Asynchronous FastAPI backend providing REST APIs, ASGI WebSockets, SQLAlchemy 2.0 Async ORM models, Pydantic v2 schemas, Redis sequence generators & idempotency, and background worker hooks.

## Key Features

- **FastAPI + Async Python 3.11+**
- **SQLAlchemy 2.0 Async ORM** with PostgreSQL 16
- **Alembic** asynchronous database migrations
- **Redis 7** idempotency locks and daily atomic sequence numbering (`KOT-YYYYMMDD-0001`)
- **Realtime WebSockets** with Redis Pub/Sub broadcast support
- **Automatic OpenAPI / TypeScript contract generation** for React Native client apps

## Local Development

```bash
# Install dependencies
pip install -e .

# Run dev server
uvicorn app.main:app --reload --port 8000
```

## Running Alembic Migrations

```bash
# Generate revision
alembic revision --autogenerate -m "initial schema"

# Apply migrations
alembic upgrade head
```
