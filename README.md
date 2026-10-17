# Enterprise Restaurant ERP Monorepo

Enterprise-grade, high-performance Restaurant ERP platform built inside a monorepo housing two React Native client applications backed by an asynchronous Python FastAPI service, PostgreSQL 16, and Redis 7.

---

## 1. Monorepo Architecture

```text
restaurant-erp/
├── apps/
│   ├── pos-app/                    # React Native POS for tablets & captain handhelds
│   └── admin-app/                  # React Native Admin for owner & manager smartphones
├── packages/
│   └── api-client/                 # Generated TypeScript types & TanStack Query hooks
├── services/
│   └── api-fastapi/                # FastAPI Async Backend (PostgreSQL 16 + Redis 7)
│       ├── alembic/                # Database migrations
│       ├── app/
│       │   ├── core/               # Config, JWT + PIN Auth, Idempotency, Redis, DB session
│       │   ├── models/             # SQLAlchemy 2.0 Async Models (PostgreSQL)
│       │   ├── schemas/            # Pydantic v2 Request/Response Schemas
│       │   ├── modules/
│       │   │   ├── catalog/        # Categories, Items, Variants, Modifiers, "86" toggle
│       │   │   ├── floor/          # Branches, Floors, Tables, Merge/Transfer/Split
│       │   │   ├── orders/         # Order lifecycle, price snapshotting, optimistic lock
│       │   │   ├── kots/           # KOT batching, station splitting, Void KOTs
│       │   │   ├── billing/        # Tax Engine (CGST/SGST), Discounts, Split Payments, UPI QR
│       │   │   ├── staff/          # Waiter CRUD, RBAC roles, Shift open/close, Blind drops
│       │   │   └── analytics/      # Live KPIs, Z-Report, Menu Engineering, Fraud Audit Log
│       │   ├── realtime/           # FastAPI WebSocket Manager + Redis Pub/Sub fan-out
│       │   └── worker/             # Tasks (BOM recipe deduction, Push alerts, EOD)
│       ├── Dockerfile
│       └── pyproject.toml
├── docker-compose.yml              # Local Postgres 16, Redis 7, and FastAPI stack
├── pnpm-workspace.yaml             # pnpm workspaces definition
└── package.json                    # Root build and API generation scripts
```

---

## 2. Type Synchronization Contract

Request and response schemas are defined once in FastAPI using **Pydantic v2**.

Running the generation command parses FastAPI's `/openapi.json` schema via `@hey-api/openapi-ts` to output strict TypeScript types and TanStack Query (`useQuery`, `useMutation`) hooks into `packages/api-client` for consumption across both React Native apps:

```bash
pnpm generate:api
```

---

## 3. Quick Start

### Prerequisites
- Node.js >= 18 and `pnpm`
- Python >= 3.11
- Docker & Docker Compose

### Start Infrastructure (PostgreSQL 16 & Redis 7)

```bash
# Start Postgres and Redis
docker compose up -d postgres redis
```

### Run FastAPI Backend

```bash
cd services/api-fastapi
pip install -e .
uvicorn app.main:app --reload --port 8000
```

The interactive OpenAPI documentation will be available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

### Run React Native Apps

```bash
# Install workspace dependencies
pnpm install

# Start POS app
pnpm --filter @restaurant-erp/pos-app start

# Start Admin app
pnpm --filter @restaurant-erp/admin-app start
```
