# @restaurant-erp/api-client

Shared TypeScript API client library and TanStack Query hooks generated from the FastAPI backend's `/openapi.json` contract.

## Generating Types and Hooks

Ensure the FastAPI backend service is running locally (`http://localhost:8000`), then run from the monorepo root:

```bash
pnpm generate:api
```

This updates `./src/generated` with:
- Strict TypeScript interface models mirroring FastAPI's Pydantic v2 schemas
- Type-safe fetch client
- TanStack Query (`useQuery`, `useMutation`) hooks for both `apps/pos-app` and `apps/admin-app`.
