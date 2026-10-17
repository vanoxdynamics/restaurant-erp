/**
 * @restaurant-erp/api-client
 * 
 * Auto-generated client package.
 * Run `pnpm generate:api` from repository root to re-generate types and TanStack Query hooks from FastAPI.
 */

export interface ApiClientConfig {
  baseUrl: string;
  authToken?: string;
}

export interface ApiResponse<T = unknown> {
  data?: T;
  error?: string;
  status: number;
}

// Placeholder export for initial package setup. Replaced or extended by generated OpenAPI clients.
export const API_BASE_URL = process.env.API_BASE_URL || 'http://localhost:8000';
