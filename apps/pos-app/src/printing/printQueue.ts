import { create } from 'zustand';
import { apiClient, KitchenTicket, newIdempotencyKey } from '@repo/api-client';
import { terminal } from '../devices/terminal';
import { useDeviceStore } from '../stores/deviceStore';
import { usePrinterSettings } from '../stores/printerStore';

/**
 * The counter's print station: KOTs print one at a time on a printer that reports its result (the Pine Labs printer, or the
 * simulator). A KOT counts as printed only when the printer says so, and is then confirmed to the server with a key kept for
 * the job, so a retried confirmation is never a second print. A printer error leaves the job failed with the reason; it is
 * tried again on Retry, or by itself after a short pause (someone may have changed the paper).
 *
 * Jobs come from the server's print queue (KOTs sent from waiters' phones, VOID notices owed to the kitchen) and straight
 * from this device when the cashier sends or reprints a KOT.
 *
 * Printing runs on the device's own credential (the token it got when it was linked), so it carries on while the screen is
 * locked. A reprint is a person's request: it goes through their session and is audited to them.
 */

export type JobKind = 'kot' | 'void' | 'reprint';
export type JobState = 'waiting' | 'printing' | 'printed' | 'failed';

export interface PrintJob {
  key: string;
  kot: KitchenTicket;
  kind: JobKind;
  state: JobState;
  error?: string;
  /** Idempotency key for confirming this print to the server. */
  confirmKey: string;
  confirmed: boolean;
  updatedAt: number;
}

const AUTO_RETRY_MS = 30_000;
const MAX_JOBS = 60;

interface PrintQueueState {
  jobs: PrintJob[];
  running: boolean;
  /** Called after each confirmed print, so screens refresh their tickets. */
  onConfirmed?: () => void;
  enqueue: (kot: KitchenTicket, kind: JobKind) => void;
  retry: (key: string) => void;
  setOnConfirmed: (fn: () => void) => void;
}

const jobKey = (kot: KitchenTicket, kind: JobKind) => (kind === 'reprint' ? `${kot.id}:reprint:${Date.now()}` : `${kot.id}:${kind}`);

export const usePrintQueueStore = create<PrintQueueState>((set, get) => {
  const update = (key: string, patch: Partial<PrintJob>) =>
    set((s) => ({ jobs: s.jobs.map((j) => (j.key === key ? { ...j, ...patch, updatedAt: Date.now() } : j)) }));

  const deviceToken = () => useDeviceStore.getState().device?.deviceToken;
  const asStation = (job: PrintJob) => job.kind !== 'reprint' && !!deviceToken();

  async function confirm(job: PrintJob) {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        if (asStation(job)) await apiClient.confirmStationPrinted(deviceToken()!, job.kot.id, job.confirmKey);
        else await apiClient.confirmSlipPrinted(job.kot.id, job.confirmKey);
        update(job.key, { confirmed: true });
        get().onConfirmed?.();
        return;
      } catch {
        await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
      }
    }
    // The paper is out but the server has not heard: try again on the next run, with the same key.
  }

  async function run() {
    if (get().running) return;
    set({ running: true });
    try {
      for (;;) {
        const unconfirmed = get().jobs.find((j) => j.state === 'printed' && !j.confirmed);
        if (unconfirmed) {
          await confirm(unconfirmed);
          if (!get().jobs.find((j) => j.key === unconfirmed.key)?.confirmed) break;
          continue;
        }
        const job = get().jobs.find((j) => j.state === 'waiting');
        if (!job) break;
        update(job.key, { state: 'printing', error: undefined });
        try {
          const paperMm = usePrinterSettings.getState().paperMm;
          const page = asStation(job)
            ? await apiClient.getStationSlipPrint(deviceToken()!, job.kot.id, paperMm)
            : await apiClient.getSlipPrint(job.kot.id, paperMm);
          const result = await terminal.print(page);
          if (!result.ok) {
            update(job.key, { state: 'failed', error: result.message ?? 'Not printed' });
            continue;
          }
          update(job.key, { state: 'printed' });
          await confirm({ ...job, state: 'printed' });
        } catch (e) {
          update(job.key, { state: 'failed', error: e instanceof Error ? e.message : 'Not printed' });
        }
      }
    } finally {
      set({ running: false });
    }
  }

  return {
    jobs: [],
    running: false,
    setOnConfirmed: (fn) => set({ onConfirmed: fn }),
    enqueue: (kot, kind) => {
      const key = jobKey(kot, kind);
      const existing = get().jobs.find((j) => j.key === key);
      if (existing) {
        // Already known. A failed job is tried again once the printer has had a moment; anything else is left alone.
        if (existing.state === 'failed' && Date.now() - existing.updatedAt > AUTO_RETRY_MS) update(key, { state: 'waiting' });
        else return;
      } else {
        const job: PrintJob = { key, kot, kind, state: 'waiting', confirmKey: newIdempotencyKey(), confirmed: false, updatedAt: Date.now() };
        set((s) => ({ jobs: [job, ...s.jobs].slice(0, MAX_JOBS) }));
      }
      void run();
    },
    retry: (key) => {
      update(key, { state: 'waiting', error: undefined });
      void run();
    },
  };
});

/** The newest job for a ticket, to show "Printing", "Printed" or the printer's error next to it. */
export function latestJobFor(jobs: PrintJob[], kotId: string): PrintJob | undefined {
  return jobs.find((j) => j.kot.id === kotId);
}
