import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@repo/api-client';
import { IS_COUNTER, printsUnattended } from '../device';
import { useDeviceStore } from '../stores/deviceStore';
import { usePrintQueueStore } from './printQueue';

const POLL_MS = 4000;

/**
 * The counter device as its branch's print station. Every few seconds it asks the server, with the device's own token, what
 * still has to print (KOTs sent from waiters' phones, VOID notices owed to the kitchen) and hands it to the print queue. It
 * does not depend on anyone being signed in, so a locked counter keeps printing.
 *
 * A plain timer rather than a cached query: signing out clears the app's query cache, and the station must not stop with it.
 */
export function usePrintStation() {
  const token = useDeviceStore((s) => s.device?.deviceToken);
  const active = IS_COUNTER && printsUnattended() && !!token;
  const enqueue = usePrintQueueStore((s) => s.enqueue);
  const setOnConfirmed = usePrintQueueStore((s) => s.setOnConfirmed);
  const queryClient = useQueryClient();

  useEffect(() => {
    setOnConfirmed(() => {
      queryClient.invalidateQueries({ queryKey: ['orderTickets'] });
      queryClient.invalidateQueries({ queryKey: ['kitchenBoard'] });
    });
  }, [queryClient, setOnConfirmed]);

  useEffect(() => {
    if (!active || !token) return;
    let stopped = false;
    const check = async () => {
      try {
        const due = await apiClient.getStationPrintQueue(token);
        if (!stopped) for (const kot of due) enqueue(kot, kot.status === 'voided' ? 'void' : 'kot');
      } catch {
        // Offline or the server is restarting: the next check catches up.
      }
    };
    void check();
    const timer = setInterval(check, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [active, token, enqueue]);
}

/** Mounts the print station inside the app's query provider, whether or not anyone is signed in. */
export function PrintStation(): null {
  usePrintStation();
  return null;
}
