import { defineConfig } from '@hey-api/openapi-ts';

export default defineConfig({
  client: '@hey-api/client-fetch',
  input: process.env.OPENAPI_URL || 'http://localhost:8000/openapi.json',
  output: {
    path: './src/generated',
    format: 'prettier',
  },
  plugins: [
    '@hey-api/client-fetch',
    '@tanstack/react-query',
  ],
});
