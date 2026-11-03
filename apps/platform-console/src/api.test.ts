import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api, readCookie, refreshSession, request, session, toApiError } from './api';
import { date, money, relativeDays } from './format';

function json(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}
const tokenResponse = (token: string) =>
  json({
    access_token: token, expires_in: 600, csrf_token: 'csrf',
    admin: { id: '1', email: 'a@b.c', full_name: 'A', role: 'super_admin', is_active: true, last_login_at: null, created_at: '' },
  });
const apiError = (status: number, code: string, message = code) => json({ error: { code, message, details: {} } }, status);

let fetchMock: ReturnType<typeof vi.fn>;
const calls = () => fetchMock.mock.calls.map(([url, init]) => ({ url: String(url), init: (init ?? {}) as RequestInit }));

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('window', { location: { origin: 'http://console.test' } });
  vi.stubGlobal('document', { cookie: 'theme=dark; pa_csrf=csrf-cookie' });
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  session.setStepUpHandler(null);
});

describe('helpers', () => {
  it('reads cookies', () => {
    expect(readCookie('pa_csrf', 'a=1; pa_csrf=x%20y; b=2')).toBe('x y');
    expect(readCookie('missing', 'a=1')).toBe('');
    expect(readCookie('pa', 'pa_csrf=1')).toBe('');
  });

  it('turns API and FastAPI validation errors into ApiError', async () => {
    const e1 = await toApiError(apiError(403, 'step_up_required', 'Re-authenticate'));
    expect(e1).toBeInstanceOf(ApiError);
    expect([e1.status, e1.code, e1.message]).toEqual([403, 'step_up_required', 'Re-authenticate']);
    const e2 = await toApiError(json({ detail: [{ loc: ['body', 'owner', 'pin'], msg: 'String should match pattern' }] }, 422));
    expect([e2.code, e2.message]).toEqual(['validation_failed', 'owner.pin: String should match pattern']);
    const e3 = await toApiError(new Response('boom', { status: 502, statusText: 'Bad Gateway' }));
    expect([e3.code, e3.message]).toEqual(['http_error', 'Bad Gateway']);
  });

  it('formats money, dates and relative days', () => {
    expect(money('10000.00')).toContain('10,000.00');
    expect(money(null)).toBe('—');
    expect(date(null)).toBe('—');
    const now = new Date('2026-01-10T00:00:00Z');
    expect(relativeDays('2026-01-13T00:00:00Z', now)).toBe('in 3 days');
    expect(relativeDays('2026-01-09T00:00:00Z', now)).toBe('1 day ago');
    expect(relativeDays('2026-01-10T00:00:00Z', now)).toBe('today');
  });
});

describe('request', () => {
  it('sends JSON, auth header and idempotency key; returns undefined for 204', async () => {
    fetchMock.mockResolvedValueOnce(tokenResponse('tok-1'));
    await refreshSession();
    fetchMock.mockResolvedValueOnce(json({ ok: 1 }));
    await request('POST', '/organizations', { body: { a: 1 }, idempotencyKey: 'key-1', query: { x: 'y', skip: undefined } });
    const { url, init } = calls()[1]!;
    expect(url).toBe('http://console.test/api/v1/platform/organizations?x=y');
    expect(init.headers).toMatchObject({ Authorization: 'Bearer tok-1', 'Content-Type': 'application/json', 'X-Idempotency-Key': 'key-1' });
    fetchMock.mockResolvedValueOnce(json(null, 204));
    expect(await request('DELETE', '/sessions/1')).toBeUndefined();
  });

  it('refreshes once on 401 and retries with the new token', async () => {
    fetchMock
      .mockResolvedValueOnce(apiError(401, 'session_ended'))
      .mockResolvedValueOnce(tokenResponse('tok-2'))
      .mockResolvedValueOnce(json({ id: 'me' }));
    expect(await request('GET', '/me')).toEqual({ id: 'me' });
    const c = calls();
    expect(c[1]!.url).toContain('/auth/refresh');
    expect(c[1]!.init.headers).toMatchObject({ 'X-CSRF-Token': 'csrf-cookie' });
    expect(c[2]!.init.headers).toMatchObject({ Authorization: 'Bearer tok-2' });
  });

  it('shares one refresh between concurrent 401s (a replayed refresh token would end the session)', async () => {
    let token = '';
    fetchMock.mockImplementation(async (url: unknown, init: RequestInit | undefined) => {
      if (String(url).includes('/auth/refresh')) {
        token = 'tok-3';
        return tokenResponse('tok-3');
      }
      const sent = (init?.headers as Record<string, string> | undefined)?.Authorization;
      return sent === `Bearer ${token}` && token ? json({ ok: true }) : apiError(401, 'session_ended');
    });
    const results = await Promise.all([request('GET', '/a'), request('GET', '/b'), request('GET', '/c')]);
    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }]);
    expect(calls().filter((c) => c.url.includes('/auth/refresh'))).toHaveLength(1);
  });

  it('gives up and surfaces the 401 when the refresh fails, and ends the session', async () => {
    fetchMock.mockResolvedValueOnce(tokenResponse('tok-4'));
    await refreshSession();
    expect(session.admin?.email).toBe('a@b.c');
    fetchMock.mockResolvedValueOnce(apiError(401, 'session_ended')).mockResolvedValueOnce(apiError(401, 'no_session'));
    await expect(request('GET', '/me')).rejects.toMatchObject({ status: 401 });
    expect(session.admin).toBeNull();
  });

  it('does not try to refresh when there is no CSRF cookie', async () => {
    vi.stubGlobal('document', { cookie: '' });
    expect(await refreshSession()).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('asks for re-authentication on step_up_required, then retries once', async () => {
    const handler = vi.fn().mockResolvedValue(undefined);
    session.setStepUpHandler(handler);
    fetchMock.mockResolvedValueOnce(apiError(403, 'step_up_required')).mockResolvedValueOnce(json({ ok: true }));
    expect(await request('POST', '/organizations/1/suspend', { body: { reason: 'abc' } })).toEqual({ ok: true });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('propagates a cancelled re-authentication and never loops', async () => {
    session.setStepUpHandler(() => Promise.reject(new ApiError(0, 'step_up_cancelled', 'cancelled')));
    fetchMock.mockImplementation(async () => apiError(403, 'step_up_required'));
    await expect(request('POST', '/x')).rejects.toMatchObject({ code: 'step_up_cancelled' });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    session.setStepUpHandler(vi.fn().mockResolvedValue(undefined));
    fetchMock.mockClear();
    fetchMock.mockImplementation(async () => apiError(403, 'step_up_required'));
    await expect(request('POST', '/x')).rejects.toMatchObject({ code: 'step_up_required' });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('builds typed API calls', async () => {
    fetchMock.mockResolvedValueOnce(json({ items: [], total: 0, limit: 25, offset: 0 }));
    await api.organizations({ q: 'cafe', status: undefined, limit: 25, offset: 0 });
    expect(calls()[0]!.url).toBe('http://console.test/api/v1/platform/organizations?q=cafe&limit=25&offset=0');
    fetchMock.mockResolvedValueOnce(json({}));
    await api.clearModule('o1', 'analytics', 'resolved');
    expect(calls()[1]!.url).toContain('/organizations/o1/modules/analytics?reason=resolved');
    expect(calls()[1]!.init.method).toBe('DELETE');
  });
});
