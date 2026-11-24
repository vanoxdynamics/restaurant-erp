import { useEffect, useState } from 'react';
import { apiClient, type CloudPaymentState } from '@repo/api-client';
import { useSessionStore } from '../stores/sessionStore';

/**
 * Charging a card/UPI payment through Pine Labs' cloud, for devices without the on-device terminal plugin: the payment is
 * already saved as pending; it is sent to the terminal, then checked every few seconds until Pine Labs gives a final answer.
 * The server records approved and failed answers itself, so a closed app never loses a payment.
 */

export interface CloudCalls {
  send(paymentId: string): Promise<CloudPaymentState>;
  check(paymentId: string): Promise<CloudPaymentState>;
  cancel(paymentId: string): Promise<CloudPaymentState>;
}

export const cloudCalls = (scope: 'billing' | 'apparel', after?: () => Promise<unknown>): CloudCalls => {
  const call = (action: 'send' | 'check' | 'cancel') => async (paymentId: string) => {
    try {
      return await apiClient.pinelabsCloud(scope, paymentId, action);
    } finally {
      await after?.();
    }
  };
  return { send: call('send'), check: call('check'), cancel: call('cancel') };
};

/** Whether this server can charge through Pine Labs' cloud. Unknown (not asked yet, or unreachable) counts as no. */
export function useCloudEnabled(skip: boolean): boolean {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    if (skip) return;
    let live = true;
    apiClient.pinelabsCloudEnabled().then((v) => live && setEnabled(v), () => undefined);
    return () => {
      live = false;
    };
  }, [skip]);
  return !skip && enabled;
}

const POLL_MS = 3000;
const GIVE_UP_MS = 6 * 60 * 1000; // Pine Labs cancels an untouched payment after 5 minutes
const MAX_FAILED_CHECKS = 5;

export interface CloudRun {
  /** Set to true to cancel the payment on the terminal (the customer changed their mind, or the cashier gave up). */
  cancelRequested: () => boolean;
  onProgress: (message: string) => void;
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Pine Labs takes a few seconds to show a cancelled payment as cancelled: ask again a few times before leaving it pending. */
async function settleCancel(calls: CloudCalls, paymentId: string): Promise<CloudPaymentState> {
  let latest = await calls.cancel(paymentId);
  for (let attempt = 0; attempt < 4 && latest.state === 'pending'; attempt += 1) {
    await wait(POLL_MS);
    try {
      latest = await calls.check(paymentId);
    } catch {
      break;
    }
  }
  return latest;
}

/** Send, then poll to a final answer. Returns `pending` or `unknown` when it could not be settled: the payment stays pending. */
export async function chargeThroughCloud(calls: CloudCalls, paymentId: string, run: CloudRun): Promise<CloudPaymentState> {
  let latest = await calls.send(paymentId);
  const started = Date.now();
  let failedChecks = 0;
  while (latest.state === 'pending' || (latest.state === 'unknown' && failedChecks < MAX_FAILED_CHECKS)) {
    if (latest.state === 'pending') {
      failedChecks = 0;
      run.onProgress(latest.message);
    } else {
      failedChecks += 1;
    }
    useSessionStore.getState().touch(); // a customer paying on the terminal is not the till sitting idle: do not lock mid-charge
    if (run.cancelRequested()) return settleCancel(calls, paymentId);
    if (Date.now() - started > GIVE_UP_MS) return latest;
    await wait(POLL_MS);
    try {
      latest = await calls.check(paymentId);
    } catch {
      failedChecks += 1;
      if (failedChecks >= MAX_FAILED_CHECKS) return { state: 'unknown', message: 'Could not reach the server to check the payment.' };
    }
  }
  return latest;
}
