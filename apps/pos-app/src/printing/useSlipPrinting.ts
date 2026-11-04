import { useState } from 'react';
import { ApiError, apiClient, KitchenTicket, newIdempotencyKey, useConfirmSlipPrinted } from '@repo/api-client';
import { printHtml } from './printHtml';
import { usePrinterSettings } from '../stores/printerStore';

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : 'The KOT could not be printed. Try again.');

interface Awaiting {
  tickets: KitchenTicket[];
  /** One key per ticket for this attempt, so retrying a dropped confirmation never counts as a reprint. */
  keys: Record<string, string>;
}

/**
 * Kitchen slips on a plan without the kitchen display. The system print dialog cannot say whether the paper came out,
 * so after it closes the waiter confirms; until then the ticket stays "not printed" on the order and the floor.
 * Firing never waits on any of this.
 */
export function useSlipPrinting(orderId: string) {
  const confirm = useConfirmSlipPrinted();
  const [awaiting, setAwaiting] = useState<Awaiting | null>(null);
  const [printing, setPrinting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const printPages = async (tickets: KitchenTicket[]) => {
    const paperMm = usePrinterSettings.getState().paperMm;
    for (const ticket of tickets) await printHtml(await apiClient.getSlipHtml(ticket.id, paperMm), paperMm);
  };

  /** Print new slips or reprints, then ask whether they came out. */
  const print = async (tickets: KitchenTicket[]) => {
    if (tickets.length === 0) return;
    setError(null);
    setPrinting(true);
    try {
      await printPages(tickets);
      setAwaiting({ tickets, keys: Object.fromEntries(tickets.map((t) => [t.id, newIdempotencyKey()])) });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setPrinting(false);
    }
  };

  /** A VOID slip is a notice to the kitchen, not a ticket to cook, so it is not counted as a print of the order. */
  const printVoid = async (ticket: KitchenTicket) => {
    setError(null);
    setPrinting(true);
    try {
      await printPages([ticket]);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setPrinting(false);
    }
  };

  const confirmPrinted = async () => {
    if (!awaiting) return;
    setError(null);
    try {
      for (const ticket of awaiting.tickets) {
        await confirm.mutateAsync({ kotId: ticket.id, idempotencyKey: awaiting.keys[ticket.id], orderId });
      }
      setAwaiting(null);
    } catch (e) {
      setError(errorText(e));
    }
  };

  return {
    print,
    printVoid,
    printing,
    /** Slips sent to the printer and waiting for "did it print?". */
    awaiting: awaiting?.tickets ?? null,
    confirmPrinted,
    confirming: confirm.isPending,
    notPrinted: () => setAwaiting(null),
    error,
  };
}
