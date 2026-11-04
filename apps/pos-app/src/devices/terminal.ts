import type { PrintablePage } from '@repo/api-client';
import { paymentKind, printerKind } from '../device';
import { useSessionStore } from '../stores/sessionStore';
import * as pinelabs from './pinelabs';
import { pineLabsNativeRequest } from './pinelabsNative';
import { simulatorTransport } from './simulator';

/**
 * The card terminal and printer of this device, if it has them: Pine Labs on the counter device, the simulator in
 * development, nothing otherwise. Screens call these functions and never deal with the transport.
 */

const APPLICATION_ID = process.env.EXPO_PUBLIC_PINELABS_APP_ID || 'PENDING-UAT-APP-ID';

function transport(kind: 'pinelabs' | 'simulator' | string): pinelabs.Transport | null {
  if (kind === 'pinelabs') return pineLabsNativeRequest;
  if (kind === 'simulator') return simulatorTransport;
  return null;
}

function context(): pinelabs.Context {
  return { applicationId: APPLICATION_ID, userId: useSessionStore.getState().user?.username ?? 'staff' };
}

const paymentTransport = () => transport(paymentKind());
const printerTransport = () => transport(printerKind());

export const hasTerminalPayments = () => paymentTransport() !== null;
export const hasLinePrinter = () => printerTransport() !== null;

const noTerminal = (): pinelabs.TransactionResult => ({ status: 'declined', message: 'This device has no card terminal.', details: {} });

export const terminal = {
  sale(mode: pinelabs.PayMode, amountPaise: number, billingRef: string) {
    const t = paymentTransport();
    return t ? pinelabs.sale(t, context(), mode, amountPaise, billingRef) : Promise.resolve(noTerminal());
  },
  status(amountPaise: number, billingRef: string) {
    const t = paymentTransport();
    return t ? pinelabs.status(t, context(), amountPaise, billingRef) : Promise.resolve(noTerminal());
  },
  voidSale(mode: pinelabs.PayMode, amountPaise: number, billingRef: string, invoiceNo: string, bankCode: string) {
    const t = paymentTransport();
    return t ? pinelabs.voidSale(t, context(), mode, amountPaise, billingRef, invoiceNo, bankCode) : Promise.resolve(noTerminal());
  },
  print(page: PrintablePage): Promise<pinelabs.PrintResult> {
    const t = printerTransport();
    return t ? pinelabs.print(t, context(), page) : Promise.resolve({ ok: false, message: 'This device has no printer.' });
  },
  settle() {
    const t = paymentTransport();
    return t ? pinelabs.settle(t, context()) : Promise.resolve({ ok: false, message: 'This device has no card terminal.', lines: [] });
  },
  info() {
    const t = paymentTransport();
    return t ? pinelabs.terminalInfo(t, context()) : Promise.resolve({} as Record<string, string>);
  },
};
