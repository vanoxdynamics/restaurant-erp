import { useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { ApiError, auth, session, type Admin, type Role } from './api';
import { Field, Modal, ErrorBox } from './ui';

// ------------------------------------------------------------------------------------------------ session state

export function useAdmin(): Admin | null {
  return useSyncExternalStore(session.subscribe, () => session.admin);
}

/** What each role may do; the server enforces the same rules, this only decides what to show. */
export function useCan() {
  const admin = useAdmin();
  const role: Role | undefined = admin?.role;
  return {
    role,
    manageBilling: role === 'super_admin' || role === 'billing',
    impersonate: role === 'super_admin' || role === 'support',
    superAdmin: role === 'super_admin',
    readAudit: role === 'super_admin' || role === 'read_only',
  };
}

type Phase = 'resuming' | 'ready';
const PhaseContext = createContext<Phase>('resuming');
export const usePhase = () => useContext(PhaseContext);

/** Tries to resume a session from the refresh cookie on first load. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>('resuming');
  const queryClient = useQueryClient();
  const admin = useAdmin();
  const hadAdmin = useRef(false);

  useEffect(() => {
    let alive = true;
    void auth.resume().finally(() => alive && setPhase('ready'));
    return () => { alive = false; };
  }, []);

  // Never leave one operator's cached data around after sign-out or session loss.
  useEffect(() => {
    if (admin) hadAdmin.current = true;
    else if (hadAdmin.current) { queryClient.clear(); hadAdmin.current = false; }
  }, [admin, queryClient]);

  return <PhaseContext.Provider value={phase}>{children}</PhaseContext.Provider>;
}

// ------------------------------------------------------------------------------------------------ re-authentication

const CANCELLED = new ApiError(0, 'step_up_cancelled', 'Re-authentication was cancelled');

/** Shows the password + authenticator prompt whenever the API demands a fresh re-authentication, then lets the call retry. */
export function StepUpProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<{ resolve: () => void; reject: (e: unknown) => void } | null>(null);
  const pendingRef = useRef(pending);
  pendingRef.current = pending;

  useEffect(() => {
    session.setStepUpHandler(() => new Promise<void>((resolve, reject) => {
      if (pendingRef.current) { // a prompt is already open: queue behind it
        const prev = pendingRef.current;
        setPending({ resolve: () => { prev.resolve(); resolve(); }, reject: (e) => { prev.reject(e); reject(e); } });
      } else setPending({ resolve, reject });
    }));
    return () => session.setStepUpHandler(null);
  }, []);

  return (
    <>
      {children}
      {pending && (
        <StepUpDialog
          onDone={() => { pending.resolve(); setPending(null); }}
          onCancel={() => { pending.reject(CANCELLED); setPending(null); }}
        />
      )}
    </>
  );
}

function StepUpDialog({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await auth.stepUp(password, code.replace(/\s/g, ''));
      onDone();
    } catch (err) {
      setError(err);
      setCode('');
      setBusy(false);
    }
  }, [password, code, onDone]);

  return (
    <Modal title="Confirm it's you" lead="This action needs a fresh sign-in check. It stays unlocked for a few minutes." onClose={onCancel}>
      <form onSubmit={submit}>
        <ErrorBox error={error} />
        <Field label="Password" htmlFor="su-pass">
          <input id="su-pass" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </Field>
        <Field label="Authenticator code" hint="The 6-digit code from your authenticator app. A code can only be used once." htmlFor="su-code">
          <input id="su-code" className="input mono" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7} value={code} onChange={(e) => setCode(e.target.value)} required />
        </Field>
        <div className="foot">
          <button type="button" className="btn" onClick={onCancel}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy || !password || code.replace(/\s/g, '').length !== 6}>{busy ? 'Checking…' : 'Confirm'}</button>
        </div>
      </form>
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------------ sign-in page

export function SignIn() {
  const [step, setStep] = useState<'password' | 'code'>('password');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mfaToken, setMfaToken] = useState('');
  const [code, setCode] = useState('');
  const [useRecovery, setUseRecovery] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function onPassword(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const challenge = await auth.login(email.trim(), password);
      setPassword(''); // do not keep the password around once it has been used
      setMfaToken(challenge.mfa_token);
      setStep('code');
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function onCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await auth.verify(mfaToken, useRecovery ? { recovery_code: code.trim() } : { code: code.replace(/\s/g, '') });
    } catch (err) {
      setError(err);
      if (err instanceof ApiError && err.code === 'mfa_expired') { setStep('password'); setCode(''); }
      setBusy(false);
    }
  }

  return (
    <main className="signin">
      <div className="card">
        <h1>Platform Console</h1>
        <p className="muted" style={{ marginTop: 0 }}>{step === 'password' ? 'Sign in to manage subscribers and modules.' : 'Enter the code from your authenticator app.'}</p>
        <ErrorBox error={error} />
        {step === 'password' ? (
          <form onSubmit={onPassword}>
            <Field label="Email" htmlFor="email">
              <input id="email" className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
            </Field>
            <Field label="Password" htmlFor="password">
              <input id="password" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </Field>
            <button className="btn primary" style={{ width: '100%' }} disabled={busy}>{busy ? 'Checking…' : 'Continue'}</button>
          </form>
        ) : (
          <form onSubmit={onCode}>
            <Field label={useRecovery ? 'Recovery code' : 'Authenticator code'} htmlFor="code" hint={useRecovery ? 'Each recovery code works once.' : undefined}>
              <input id="code" className="input mono" inputMode={useRecovery ? 'text' : 'numeric'} autoComplete="one-time-code" value={code}
                onChange={(e) => setCode(e.target.value)} maxLength={useRecovery ? 12 : 7} required autoFocus />
            </Field>
            <button className="btn primary" style={{ width: '100%' }} disabled={busy}>{busy ? 'Verifying…' : 'Sign in'}</button>
            <p style={{ textAlign: 'center', marginBottom: 0 }}>
              <button type="button" className="btn ghost small" onClick={() => { setUseRecovery(!useRecovery); setCode(''); setError(null); }}>
                {useRecovery ? 'Use authenticator app' : 'Use a recovery code'}
              </button>
              <button type="button" className="btn ghost small" onClick={() => { setStep('password'); setCode(''); setError(null); }}>Start over</button>
            </p>
          </form>
        )}
      </div>
    </main>
  );
}
