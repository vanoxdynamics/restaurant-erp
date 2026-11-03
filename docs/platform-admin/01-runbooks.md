# Platform operations runbooks

Audience: the people who run the platform console and the API behind it. Commands run from `services/api-fastapi`.

## 1. Deployment checklist

**Secrets (all unique, 32+ characters, never reused between purposes).** The API refuses to start in production without them.

| Setting | Purpose | Rotating it |
|---|---|---|
| `SECRET_KEY` | signs restaurant staff tokens | everyone is signed out once; safe |
| `PIN_PEPPER` | keys the staff PIN lookup | **breaks every PIN**; only rotate after a compromise, then reset all PINs |
| `PLATFORM_SECRET_KEY` | signs operator tokens | every operator is signed out once; safe |
| `PLATFORM_ENCRYPTION_KEY` | encrypts operator authenticator secrets, keys recovery-code hashes | see section 3 |
| `PLATFORM_ALERT_WEBHOOK_SECRET` | signs alert webhooks | update the receiver at the same time |

**Database.**
- Run the API as an ordinary role, **not** a superuser, so row-level security actually binds it. Either connect as that role,
  or connect as a login role and set `DB_SET_ROLE=<app role>`. Check `GET /health/ready` shows `"rls": "enforced"`; the API logs a
  warning (an error in production) at startup when it is not.
- The app role needs `SELECT/INSERT/UPDATE/DELETE` on all tables, `USAGE` on sequences, and nothing on `platform_audit_logs`
  beyond `SELECT/INSERT` (revoke `UPDATE/DELETE/TRUNCATE`; a trigger already blocks update/delete, grants are the second lock).
- Run migrations with a separate owner role, not the app role.

**Network.**
- Console and API behind one reverse proxy on one origin (see `apps/platform-console/README.md`). IP allowlist or VPN in front.
- `PLATFORM_IP_ALLOWLIST` as a second gate; uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy>`; proxy overwrites `X-Forwarded-For`.
- `client_max_body_size 256k;` at the proxy (the API enforces `PLATFORM_MAX_BODY_BYTES` too).

**Scheduled jobs** (one of each is enough; several are safe).
- `python -m app.cli run-billing --loop` (or a cron/CronJob every hour): renewal invoices, reminders, past-due and grace.
- `python -m app.cli verify-audit` **daily**; exit status 1 and a critical alert mean the audit log was altered. Store the printed
  head hash somewhere the application cannot write to, so deleting the newest entries is detectable too.
- `python -m app.cli dispatch-alerts` every 5 minutes, to retry alert webhooks that failed.

**First operator.** `python -m app.cli create-platform-admin --email ... --full-name ...` (prints the authenticator key and recovery
codes once). Create a **second** super admin straight away: four-eyes approval needs two, and one is a single point of failure.

**Before go-live.** Set `PLATFORM_ALERT_WEBHOOK_URL`; replace the log-only `Mailer` in `billing_service.py` with a real one; confirm
Redis and Postgres backups; run the test suite once with `RLS_TEST_ROLE=rls_app` (section 6).

## 2. Alerts and what to do about them

Alerts appear in the console (Security > Alerts), in the logs (`PLATFORM ALERT`), and at the webhook.

| Alert | Severity | Meaning | Do |
|---|---|---|---|
| `auth.lockout` | warning | repeated failed sign-ins locked an account or an IP | look at the IP in the audit log; a burst against one account is guessing; check the owner is fine |
| `auth.lockout_2fa` | critical | someone knows a password but fails the second factor | assume the password leaked: deactivate or force a password change (section 4), review recent sessions |
| `auth.refresh_reuse` | critical | a used refresh token came back: session theft or a broken client | the session is already ended; treat as device compromise for that operator; rotate their password and 2FA |
| `auth.lockout_step_up` | critical | repeated failed re-authentication inside a signed-in session | the session may be hijacked: end it (Team > sessions), reset the operator's credentials |
| `admin.created` / `admin.changed` | warning | an operator was added, re-roled or deactivated | confirm it was intended; anything unexpected is an incident |
| `approval.requested` / `approval.executed` | warning | a dangerous action awaits or received a second approval | review the request; approve or reject |
| `approval.unavailable` | warning | a dangerous action ran with no second approver (only one super admin) | add a second super admin |
| `org.impersonation` | info | support opened a read-only view of a subscriber | check the reason matches a ticket |
| `audit.chain_broken` | critical | audit log history was altered | section 5 |
| `admin.2fa_reset` | critical | the break-glass command ran | confirm it was you or a colleague on purpose |

## 3. Key rotation

**`PLATFORM_ENCRYPTION_KEY`** (authenticator secrets are encrypted with it; recovery-code hashes are keyed by it):
1. Generate a new key. Set `PLATFORM_ENCRYPTION_KEY=<new>` and `PLATFORM_ENCRYPTION_KEYS_OLD=<previous>`; deploy. Logins and recovery
   codes keep working.
2. `python -m app.cli rotate-encryption-key` re-encrypts every stored authenticator secret under the new key.
3. Remove `PLATFORM_ENCRYPTION_KEYS_OLD`; deploy. **Unused recovery codes stop working** at this point (their plaintext is not stored, so
   they cannot be re-hashed): ask operators to generate new ones (a super admin can run `reset-platform-admin-2fa`, or re-create the operator).

**`PLATFORM_SECRET_KEY` / `SECRET_KEY`:** change and deploy; everyone signs in again. Do it on a schedule (for example yearly) and
immediately after any suspected exposure.

## 4. Incident playbooks

**An operator account may be compromised.**
1. A *different* super admin deactivates it (Team > deactivate). That ends its sessions at once.
2. Review the audit log for that operator's actions since the suspected time (filter by action; open entries for before/after).
3. Undo anything unwanted: reinstate organizations, clear module overrides, void invoices, revoke approvals (reject pending ones).
4. If the password was exposed, re-create the operator with a new password and new authenticator.
5. If a super admin is involved, also rotate `PLATFORM_SECRET_KEY` (signs everyone out) and review `admin.created` alerts.

**Lost phone / lost authenticator.** An operator with a recovery code signs in with it (each works once) and Team > add a fresh setup.
With no recovery code: another super admin cannot see secrets, so run on the server
`python -m app.cli reset-platform-admin-2fa --email <them>`, which asks you to retype the email, ends their sessions, issues a new
authenticator key and recovery codes, and raises a critical alert.

**The last super admin is locked out.** `python -m app.cli create-platform-admin --email <new>` creates another super admin (requires
server access, which is the point), then deactivate the old one if needed.

**A subscriber must be cut off now (abuse, chargeback, legal).** Subscribers > the subscriber > Suspend (re-authenticate, give a
reason). Every session is revoked and open connections are closed immediately; nothing works until you reinstate. Their data is untouched.

**A module is misbehaving for everyone.** Modules > switch it off platform-wide. This needs a second super admin's approval (or runs
immediately if you are the only one, which raises `approval.unavailable`). Subscribers lose it on their next request.

**Payments recorded wrongly.** Payments cannot be edited. If an invoice has no payments, void it (reason required). If it has payments,
the mistake is corrected by a new invoice and credit; note it in the payment's note field. There is no refund workflow yet.

## 5. When `audit.chain_broken` fires

1. Do not restart or "fix" anything yet. Run `python -m app.cli verify-audit` and note the first bad entry number.
2. Compare with the head hash you stored from the previous healthy run. Entries after the break are untrustworthy.
3. Restore the audit table from the last known-good backup into a scratch database and diff it against production to see what changed.
4. Treat as a database compromise: rotate DB credentials, review who had write access, review the app role's grants (it must not have
   UPDATE/DELETE on `platform_audit_logs`).
5. Record the incident out-of-band; once resolved, the chain stays broken at that entry by design (history is never rewritten), so keep the
   note with the exported head hash.

## 6. Verifying row-level security

```bash
# whole suite as a restricted role (creates the role and a separate test database for you)
RLS_TEST_ROLE=rls_app TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/restaurant_erp_test_rls \
  TEST_REDIS_URL=redis://localhost:6379/14 python -m pytest -q
```

Policies cover every table with an `organization_id` or `branch_id` plus `organizations`. Authenticated tenant requests set
`app.org_scope` (transaction-local, so it cannot leak through the connection pool). Platform, login and worker code runs with no scope and
sees everything, **so row-level security protects the tenant API, not the platform API**. Child tables without either column
(`order_items`, `kot_items`, `dining_tables`, `item_variants`, ...) are reached only through scoped parents.

## 7. Performance

`python scripts/bench_entitlements.py` measures the per-request cost of authentication, the entitlement lookup and the RLS scope on a
throw-away database. Re-run it after changes to `get_current_user`, `compute_entitlements` or the policies.

## 8. Known limits (honest list)

- **No passkeys/WebAuthn yet.** Operators use password + authenticator app (TOTP). Passkeys are a planned upgrade; TOTP codes can be
  phished, passkeys cannot.
- **No breached-password check.** Passwords need 14+ characters and complexity but are not compared with known-leaked lists.
- **Email is not delivered.** Billing reminders go to an outbox; the default sender only logs.
- **Alerts have no escalation or paging**, only a signed webhook. Point it at something that pages (Slack, PagerDuty, Opsgenie).
- **Four-eyes needs two super admins.** With one, dangerous actions run immediately and raise `approval.unavailable`.
- **Redis is on the sign-in path** (rate limits). If Redis is down, operators cannot sign in; entitlement lookups fall back to the database.
- **Row-level security is fail-open when unscoped** (by design: platform, login and workers need it) and does not bind superusers.
- **No retention/purge flow** for cancelled subscribers, and no payment gateway or refunds.
