import react from '@vitejs/plugin-react';
import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

// Production builds get a strict Content-Security-Policy. It is skipped in dev because Vite's hot reload injects inline scripts.
const csp = (): Plugin => ({
  name: 'console-csp',
  apply: 'build',
  transformIndexHtml: () => [
    {
      tag: 'meta',
      attrs: {
        'http-equiv': 'Content-Security-Policy',
        content:
          "default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'self'",
      },
      injectTo: 'head-prepend',
    },
    { tag: 'meta', attrs: { name: 'referrer', content: 'no-referrer' }, injectTo: 'head' },
  ],
});

// The console and the API must share an origin so the browser sends the refresh cookie and the page can read the
// CSRF cookie. In dev Vite proxies /api; in production put both behind one reverse proxy.
const apiTarget = process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react(), csp()],
  server: { port: 5180, strictPort: true, proxy: { '/api': { target: apiTarget, changeOrigin: false } } },
  preview: { port: 5180, strictPort: true, proxy: { '/api': { target: apiTarget, changeOrigin: false } } },
  build: { sourcemap: false, target: 'es2022' },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
