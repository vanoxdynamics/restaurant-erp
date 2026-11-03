# Platform Console

Web console for platform operators: subscribers, billing terms (monthly/yearly), modules, plans, the audit log and
operator accounts. It talks to the API under `/api/v1/platform` and is separate from the restaurant apps.

## Run it locally

```bash
# 1. API (from services/api-fastapi), with the platform keys set or the development defaults
python -m app.cli create-platform-admin --email you@example.com --full-name "Your Name"   # once; prints the authenticator key
# 2. Console
pnpm --filter @restaurant-erp/platform-console dev        # http://localhost:5180, proxies /api to http://127.0.0.1:8000
```

Point it at another API with `VITE_API_TARGET=http://127.0.0.1:8010`. In development the API must run with
`PLATFORM_COOKIE_SECURE=false` if your browser refuses `Secure` cookies on plain `http://localhost` (Safari does).

Other commands: `pnpm --filter @restaurant-erp/platform-console typecheck | test | build`.

## Deploy it (read this)

**The console and the API must be served from the same origin**, for example `https://console.example.com/` for the
static files and `https://console.example.com/api/` forwarded to the API. The refresh token is an `HttpOnly`,
`SameSite=Strict` cookie and the CSRF token is a cookie the page must be able to read; both only work when the page
and the API share a host. The console calls relative `/api/...` URLs for the same reason.

Minimal nginx sketch:

```nginx
server {
    listen 443 ssl http2;
    server_name console.example.com;

    # Network gate: only the office / VPN may even see the console.
    allow 203.0.113.0/24;
    deny  all;

    root /srv/platform-console/dist;                 # output of `pnpm build`
    location / { try_files $uri /index.html; }

    location /api/ {
        proxy_pass http://api:8000;                  # uvicorn --proxy-headers --forwarded-allow-ips="<proxy ip>"
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;   # overwrite, never append client-supplied values
        proxy_set_header X-Forwarded-Proto https;
    }

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy no-referrer always;
}
```

Also:

- Keep `/api/v1/platform/*` off your public tenant hostname. Set `PLATFORM_IP_ALLOWLIST` on the API as a second gate.
- Run uvicorn with `--proxy-headers --forwarded-allow-ips=<the proxy's IP>` so rate limits and the allowlist see real client IPs.
- Required secrets in production: `PLATFORM_SECRET_KEY`, `PLATFORM_ENCRYPTION_KEY` (32+ characters each, different from every other key) and `PLATFORM_COOKIE_SECURE=true`. The API refuses to start otherwise.
- The production build embeds a strict Content-Security-Policy (`script-src 'self'`, `connect-src 'self'`, no inline scripts).

## How it behaves

- The access token (10 minutes) lives only in memory. The page resumes a session after a reload from the refresh cookie.
- Refreshing is single-flight in a tab and serialised across tabs with the Web Locks API: the server treats a replayed
  refresh token as theft and ends the session, so two tabs must never refresh with the same cookie at once.
- Sensitive actions (suspend, reinstate, cancel, impersonation, the platform-wide switch, applying a bulk change,
  managing operators) make the API answer `step_up_required`. The console shows a password + authenticator prompt and
  retries the action; it stays unlocked for 5 minutes.
- Money-affecting calls (create subscriber, change term/plan/price, extend, activate) carry an idempotency key made when
  the dialog opens, so a double click or retry never applies twice. Subscription edits also send the `version` they were
  based on; a stale form gets a clear conflict instead of overwriting someone else's change.
- Menus and buttons are hidden by role, but the API enforces every rule; hiding is only a courtesy.
- Secrets (a new operator's authenticator key and recovery codes, an impersonation token) are shown once in a dialog
  and never stored by the console.
