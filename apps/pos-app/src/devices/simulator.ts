import { create } from 'zustand';
import { METHOD, TXN } from './pinelabs';

/**
 * A stand-in for Pine Labs' Plutus app, for development in the browser or an emulator (EXPO_PUBLIC_PINELABS_SIMULATOR=1).
 * It answers the same JSON as the real app. Payments wait for the developer to choose Approve, Decline or "No answer"
 * (money taken but the answer lost, to exercise recovery). Printed pages are kept for an on-screen preview, and the printer
 * can be made to fail.
 */

export type SimDecision = 'approve' | 'decline' | 'no_answer';

export interface SimPrompt {
  kind: 'card' | 'upi' | 'card void' | 'upi void';
  amountPaise: number;
  billingRef: string;
  decide: (decision: SimDecision) => void;
}

export interface SimPage {
  ref: string;
  at: number;
  lines: { text: string; center: boolean; big: boolean; bold: boolean }[];
}

interface LedgerEntry {
  mode: 'card' | 'upi';
  amountPaise: number;
  approvalCode: string;
  invoiceNo: number;
  voided: boolean;
  settled: boolean;
}

interface SimState {
  prompt: SimPrompt | null;
  printerFault: string | null; // a Pine Labs printer code, e.g. '1002' out of paper
  paper: SimPage[];
  ledger: Record<string, LedgerEntry>;
  setPrinterFault: (code: string | null) => void;
}

export const useSimulator = create<SimState>((set) => ({
  prompt: null,
  printerFault: null,
  paper: [],
  ledger: {},
  setPrinterFault: (code) => set({ printerFault: code }),
}));

let invoiceSeq = 100;
const randomDigits = (n: number) => Array.from({ length: n }, () => Math.floor(Math.random() * 10)).join('');
const answer = (code: string, msg: string, detail?: Record<string, unknown>) =>
  JSON.stringify({ Response: { ResponseCode: code, ResponseMsg: msg }, ...(detail ? { Detail: detail } : {}) });

function ask(kind: SimPrompt['kind'], amountPaise: number, billingRef: string): Promise<SimDecision> {
  return new Promise((resolve) => {
    useSimulator.setState({
      prompt: {
        kind,
        amountPaise,
        billingRef,
        decide: (decision) => {
          useSimulator.setState({ prompt: null });
          resolve(decision);
        },
      },
    });
  });
}

function saleDetail(ref: string, entry: LedgerEntry) {
  return {
    BillingRefNo: ref,
    ApprovalCode: entry.approvalCode,
    CardNumber: entry.mode === 'card' ? '************1111' : '',
    CardType: entry.mode === 'card' ? 'VISA' : 'UPI',
    InvoiceNumber: entry.invoiceNo,
    BatchNumber: 9001,
    TerminalId: 'SIM00001',
    MerchantId: 'SIMMERCHANT01',
    AcquiringBankCode: '01',
    AcquirerName: 'HDFC BANK',
    RetrievalReferenceNumber: randomDigits(12),
  };
}

export async function simulatorTransport(requestJson: string): Promise<string> {
  const request = JSON.parse(requestJson);
  const method = String(request?.Header?.MethodId ?? '');
  const detail = request?.Detail ?? {};
  const state = useSimulator.getState();

  if (method === METHOD.DO_TRANSACTION) {
    const type = Number(detail.TransactionType);
    const ref = String(detail.BillingRefNo ?? '');
    const amount = Number(detail.PaymentAmount ?? 0);

    if (type === TXN.GET_STATUS) {
      const entry = state.ledger[ref];
      return entry && !entry.voided ? answer('0', 'APPROVED', saleDetail(ref, entry)) : answer('1', 'TRANSACTION NOT FOUND');
    }
    if (type === TXN.CARD_SALE || type === TXN.UPI_SALE) {
      const mode = type === TXN.CARD_SALE ? 'card' : 'upi';
      const decision = await ask(mode, amount, ref);
      if (decision === 'decline') return answer('1', mode === 'card' ? 'DECLINED BY BANK' : 'UPI PAYMENT NOT RECEIVED');
      const entry: LedgerEntry = { mode, amountPaise: amount, approvalCode: randomDigits(6), invoiceNo: ++invoiceSeq, voided: false, settled: false };
      useSimulator.setState((s) => ({ ledger: { ...s.ledger, [ref]: entry } }));
      if (decision === 'no_answer') throw new Error('No answer from the terminal (simulated: the money was taken)');
      return answer('0', 'APPROVED', saleDetail(ref, entry));
    }
    if (type === TXN.CARD_VOID || type === TXN.UPI_VOID) {
      const entry = state.ledger[ref];
      if (!entry || entry.voided || entry.settled) return answer('1', 'ORIGINAL TRANSACTION NOT FOUND');
      const decision = await ask(type === TXN.CARD_VOID ? 'card void' : 'upi void', amount, ref);
      if (decision !== 'approve') return answer('1', 'VOID NOT APPROVED');
      useSimulator.setState((s) => ({ ledger: { ...s.ledger, [ref]: { ...entry, voided: true } } }));
      return answer('0', 'VOID APPROVED', { ...saleDetail(ref, entry), ApprovalCode: randomDigits(6) });
    }
    return answer('3', 'Invalid transaction type');
  }

  if (method === METHOD.PRINT_DATA) {
    if (state.printerFault) return answer(state.printerFault, 'PRINTER ERROR');
    const page: SimPage = {
      ref: String(detail.PrintRefNo ?? ''),
      at: Date.now(),
      lines: (detail.Data ?? []).map((d: any) => ({
        text: String(d.DataToPrint ?? ''),
        center: !!d.IsCenterAligned,
        big: Number(d.PrinterWidth) === 24,
        bold: Number(d.textStyle) === 1,
      })),
    };
    useSimulator.setState((s) => ({ paper: [page, ...s.paper].slice(0, 20) }));
    return answer('0', 'SUCCESS', { AppVersion: 'Simulator' });
  }

  if (method === METHOD.SETTLEMENT) {
    const open = Object.entries(state.ledger).filter(([, e]) => !e.voided && !e.settled);
    const cards = open.filter(([, e]) => e.mode === 'card');
    const settledLedger = Object.fromEntries(Object.entries(state.ledger).map(([k, e]) => [k, { ...e, settled: true }]));
    useSimulator.setState({ ledger: settledLedger });
    return answer('0', 'SETTLEMENT SUCCESS', {
      SettlementSummary: [{
        BatchName: 'HDFC', AcquirerCode: '01', TID: 'SIM00001', MID: 'SIMMERCHANT01',
        CreditCount: 0, CreditAmount: 0, DebitCount: cards.length, DebitAmount: cards.reduce((n, [, e]) => n + e.amountPaise, 0),
      }],
    });
  }

  if (method === METHOD.TERMINAL_INFO) {
    return answer('0', 'SUCCESS', { PlutusStoreId: 'SIM-STORE', PlutusTerminalId: 'SIM00001', SerialNumber: 'SIM-0001', MerchantName: 'Simulated merchant', StoreName: 'Simulator' });
  }
  return answer('3', 'Invalid Method Id');
}
