import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiError } from './api';
import { label } from './format';

// ------------------------------------------------------------------------------------------------ toasts

interface Toast {
  id: number;
  text: string;
  error: boolean;
}
const ToastContext = createContext<(text: string, error?: boolean) => void>(() => undefined);
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counter = useRef(0);
  const push = useCallback((text: string, error = false) => {
    const id = ++counter.current;
    setToasts((t) => [...t, { id, text, error }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), error ? 7000 : 3500);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast${t.error ? ' error' : ''}`}>{t.text}</div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// ------------------------------------------------------------------------------------------------ pieces

export function Spinner() {
  return <span className="spinner" role="progressbar" aria-label="Loading" />;
}

export function Loading() {
  return <div className="center"><Spinner /></div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof ApiError ? error.message : error instanceof Error ? error.message : 'Something went wrong';
  return <div className="alert error" role="alert">{message}</div>;
}

const TONES: Record<string, string> = {
  active: 'good', full: 'good', trialing: 'info', trial: 'info', past_due: 'warn', read_only: 'warn', paused: 'warn',
  suspended: 'bad', cancelled: 'bad', expired: 'bad', blocked: 'bad', platform_disabled: 'bad',
  super_admin: 'info', billing: 'info', support: 'info',
};
export function Badge({ value, tone }: { value: string | null | undefined; tone?: string }) {
  if (!value) return <span className="muted">—</span>;
  return <span className={`badge ${tone ?? TONES[value] ?? ''}`}>{label(value)}</span>;
}

export function Field({ label: text, hint, children, htmlFor }: { label: string; hint?: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{text}</label>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

export function Switch({ checked, onChange, disabled, label: text }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <label className="switch">
      <input type="checkbox" role="switch" aria-label={text} checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span />
    </label>
  );
}

export function Segmented<T extends string>({ value, options, onChange, name }: { value: T; options: Array<{ value: T; label: string }>; onChange: (v: T) => void; name: string }) {
  return (
    <div className="segmented" role="group" aria-label={name}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

export function PageHead({ title, sub, children }: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <div className="sub">{sub}</div>}
      </div>
      {children && <div className="actions">{children}</div>}
    </div>
  );
}

export function Pager({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (offset: number) => void }) {
  if (total <= limit) return <div className="pager"><span>{total} result{total === 1 ? '' : 's'}</span></div>;
  return (
    <div className="pager">
      <span>{offset + 1}–{Math.min(offset + limit, total)} of {total}</span>
      <span className="actions">
        <button className="btn small" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>Previous</button>
        <button className="btn small" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>Next</button>
      </span>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ modal

export function Modal({ title, lead, onClose, children, wide }: { title: string; lead?: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>('input:not([type=hidden]), select, textarea, button');
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'Tab' && ref.current) { // keep focus inside the dialog
        const items = [...ref.current.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href]')].filter((el) => !el.hasAttribute('disabled'));
        if (items.length === 0) return;
        const firstEl = items[0]!, lastEl = items[items.length - 1]!;
        if (e.shiftKey && document.activeElement === firstEl) { e.preventDefault(); lastEl.focus(); }
        else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); previous?.focus(); };
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal${wide ? ' wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref}>
        <h2 id={titleId}>{title}</h2>
        {lead && <p className="lead">{lead}</p>}
        {children}
      </div>
    </div>
  );
}

/** A modal asking for a mandatory reason before a sensitive action. */
export function ReasonDialog({ title, lead, confirm, danger, busy, error, onSubmit, onClose, children }: {
  title: string; lead?: ReactNode; confirm: string; danger?: boolean; busy?: boolean; error?: unknown;
  onSubmit: (reason: string) => void; onClose: () => void; children?: ReactNode;
}) {
  const [reason, setReason] = useState('');
  const valid = reason.trim().length >= 3;
  return (
    <Modal title={title} lead={lead} onClose={onClose}>
      <form onSubmit={(e) => { e.preventDefault(); if (valid && !busy) onSubmit(reason.trim()); }}>
        <ErrorBox error={error} />
        {children}
        <Field label="Reason" hint="Recorded in the audit log. At least 3 characters." htmlFor="reason">
          <textarea id="reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} required />
        </Field>
        <div className="foot">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className={`btn ${danger ? 'danger' : 'primary'}`} disabled={!valid || busy}>{busy ? 'Working…' : confirm}</button>
        </div>
      </form>
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------------ misc hooks

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function useFormState<T extends object>(initial: T) {
  const [state, setState] = useState(initial);
  return useMemo(() => ({
    state,
    set: <K extends keyof T>(key: K, value: T[K]) => setState((s) => ({ ...s, [key]: value })),
    reset: () => setState(initial),
  }), [state, initial]);
}

export function CopyButton({ text, label: text2 = 'Copy' }: { text: string; label?: string }) {
  const toast = useToast();
  return (
    <button type="button" className="btn small" onClick={() => navigator.clipboard.writeText(text).then(() => toast('Copied'), () => toast('Copy failed', true))}>
      {text2}
    </button>
  );
}
