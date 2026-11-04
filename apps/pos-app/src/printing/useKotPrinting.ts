import type { KitchenTicket } from '@repo/api-client';
import { printerKind, printsUnattended } from '../device';
import { latestJobFor, PrintJob, usePrintQueueStore } from './printQueue';
import { useSlipPrinting } from './useSlipPrinting';

/**
 * How KOTs print on this device, in one place for the order screen:
 *
 * - `queue`: the counter device with a printer that reports its result. KOTs go to the print queue; no question asked.
 * - `dialog`: a counter on a browser or tablet using the system print dialog; staff confirm whether it printed.
 * - `counter`: a waiter's phone. Nothing prints here; the counter prints it.
 */
export type KotPrintMode = 'queue' | 'dialog' | 'counter';

export function useKotPrinting(orderId: string) {
  const mode: KotPrintMode = printsUnattended() ? 'queue' : printerKind() === 'system' ? 'dialog' : 'counter';
  const dialog = useSlipPrinting(orderId);
  const jobs = usePrintQueueStore((s) => s.jobs);
  const enqueue = usePrintQueueStore((s) => s.enqueue);
  const retry = usePrintQueueStore((s) => s.retry);

  return {
    mode,
    /** Newly sent paper KOTs. */
    printNew(tickets: KitchenTicket[]) {
      if (mode === 'queue') tickets.forEach((t) => enqueue(t, 'kot'));
      else if (mode === 'dialog') void dialog.print(tickets);
    },
    reprint(ticket: KitchenTicket) {
      if (mode === 'queue') enqueue(ticket, ticket.print_count > 0 ? 'reprint' : 'kot');
      else if (mode === 'dialog') void dialog.print([ticket]);
    },
    printVoid(ticket: KitchenTicket) {
      if (mode === 'queue') enqueue(ticket, 'void');
      // The server tracks whether the VOID notice reached the kitchen, so it is confirmed like any KOT.
      else if (mode === 'dialog') void dialog.print([ticket]);
    },
    jobFor: (kotId: string): PrintJob | undefined => (mode === 'queue' ? latestJobFor(jobs, kotId) : undefined),
    retry,
    dialog,
  };
}
