import type { PrintablePage } from '@repo/api-client';

/**
 * Pine Labs App-to-App protocol (Plutus Smart). Our app sends JSON to Pine Labs' app on the same device and gets JSON back;
 * see docs/pos-devices/02-pinelabs-integration.md. This file only builds requests and reads answers, so it runs anywhere and is
 * unit-testable; the transport (the native module, or the development simulator) is passed in.
 */

export const METHOD = {
  DO_TRANSACTION: '1001',
  PRINT_DATA: '1002',
  SETTLEMENT: '1003',
  TERMINAL_INFO: '1004',
} as const;

export const TXN = {
  CARD_SALE: 4001,
  CARD_VOID: 4006,
  UPI_SALE: 5120,
  UPI_VOID: 5121,
  GET_STATUS: 5122,
} as const;

/** Pine Labs' response code meaning "started; ask Get Status for the result". */
const CHECK_STATUS = '100';

const PRINTER_ERRORS: Record<string, string> = {
  '1': 'Printing failed',
  '1001': 'Printer is busy',
  '1002': 'Printer is out of paper',
  '1003': 'Printer is low on paper',
  '1004': 'Battery too low to print',
  '1005': 'Printer hardware error',
  '1006': 'Printer is overheated',
  '1007': 'Printer buffer overflow',
  '1008': 'Paper is not aligned',
  '1009': 'Paper is jammed',
  '1010': 'Paper cutter is out of position',
};

export type Transport = (requestJson: string) => Promise<string>;

export interface Context {
  /** Issued by Pine Labs for our app. */
  applicationId: string;
  /** The signed-in staff member. */
  userId: string;
}

export interface Answer {
  ok: boolean;
  code: string;
  message: string;
  detail: Record<string, unknown>;
}

export function buildRequest(ctx: Context, methodId: string, detail?: Record<string, unknown>): string {
  const header = { ApplicationId: ctx.applicationId, UserId: ctx.userId.slice(0, 100), MethodId: methodId, VersionNo: '1.0' };
  return JSON.stringify(detail ? { Header: header, Detail: detail } : { Header: header });
}

export function readAnswer(json: string): Answer {
  let parsed: any;
  try {
    parsed = JSON.parse(json);
  } catch {
    return { ok: false, code: 'invalid_response', message: 'Pine Labs sent an answer we could not read.', detail: {} };
  }
  const response = parsed?.Response ?? {};
  const code = String(response.ResponseCode ?? parsed?.ResponseCode ?? '');
  const message = String(response.ResponseMsg ?? response.ResponseMessage ?? parsed?.ResponseMsg ?? '');
  return { ok: code === '0', code, message, detail: parsed?.Detail ?? {} };
}

async function call(transport: Transport, ctx: Context, methodId: string, detail?: Record<string, unknown>): Promise<Answer> {
  return readAnswer(await transport(buildRequest(ctx, methodId, detail)));
}

// --- payments -----------------------------------------------------------------------------------------------------------------

export type PayMode = 'card' | 'upi';

export interface TransactionResult {
  /** approved: money taken; declined: not taken; unknown: the terminal did not say (check status, or resolve by hand). */
  status: 'approved' | 'declined' | 'unknown';
  approvalCode?: string;
  message: string;
  /** What we keep: the server drops anything else and masks the card number. */
  details: Record<string, string | number | boolean | null>;
}

const KEPT = [
  'BillingRefNo', 'ApprovalCode', 'HostResponse', 'CardNumber', 'CardType', 'InvoiceNumber', 'BatchNumber', 'TerminalId',
  'MerchantId', 'RetrievalReferenceNumber', 'AcquirerName', 'AcquiringBankCode', 'CardEntryMode', 'TransactionType', 'Remark',
  'PlutusVersion', 'PlutusTransactionLogID', 'TransactionDate', 'TransactionTime',
];

export function readTransaction(answer: Answer): TransactionResult {
  const details: TransactionResult['details'] = { ResponseCode: answer.code, ResponseMsg: answer.message };
  for (const key of KEPT) {
    const value = answer.detail[key];
    if (value !== undefined && value !== null && value !== '') details[key] = typeof value === 'object' ? JSON.stringify(value) : (value as string | number | boolean);
  }
  const approvalCode = String(answer.detail.ApprovalCode ?? '').trim();
  const message = answer.message || String(answer.detail.Remark ?? answer.detail.HostResponse ?? '');
  // Pine Labs: success needs response code 0 *and* a non-empty approval code. Code 100 means "ask for the status".
  if (answer.ok && approvalCode) return { status: 'approved', approvalCode, message: message || 'Approved', details };
  if (answer.code === CHECK_STATUS || answer.code === 'invalid_response') return { status: 'unknown', message: message || 'Result not known yet', details };
  return { status: 'declined', message: message || 'Not approved', details };
}

/** A transport failure (Plutus not answering, the app being killed) means we do not know whether money was taken. */
const unknown = (error: unknown): TransactionResult => ({
  status: 'unknown',
  message: error instanceof Error ? error.message : 'No answer from the terminal',
  details: {},
});

export async function sale(transport: Transport, ctx: Context, mode: PayMode, amountPaise: number, billingRef: string): Promise<TransactionResult> {
  try {
    return readTransaction(await call(transport, ctx, METHOD.DO_TRANSACTION, {
      TransactionType: mode === 'card' ? TXN.CARD_SALE : TXN.UPI_SALE,
      BillingRefNo: billingRef.slice(0, 10),
      PaymentAmount: Math.round(amountPaise),
      IsSwipe: true,
    }));
  } catch (e) {
    return unknown(e);
  }
}

export async function status(transport: Transport, ctx: Context, amountPaise: number, billingRef: string): Promise<TransactionResult> {
  try {
    return readTransaction(await call(transport, ctx, METHOD.DO_TRANSACTION, {
      TransactionType: TXN.GET_STATUS,
      BillingRefNo: billingRef.slice(0, 10),
      PaymentAmount: Math.round(amountPaise),
      IsSwipe: true,
    }));
  } catch (e) {
    return unknown(e);
  }
}

/** Void the same day, before settlement. Needs the original invoice number and bank code from the sale. */
export async function voidSale(
  transport: Transport, ctx: Context, mode: PayMode, amountPaise: number, billingRef: string, invoiceNo: string, bankCode: string,
): Promise<TransactionResult> {
  try {
    return readTransaction(await call(transport, ctx, METHOD.DO_TRANSACTION, {
      TransactionType: mode === 'card' ? TXN.CARD_VOID : TXN.UPI_VOID,
      BillingRefNo: billingRef.slice(0, 10),
      PaymentAmount: Math.round(amountPaise),
      InvoiceNo: invoiceNo,
      BankCode: bankCode,
      IsSwipe: true,
    }));
  } catch (e) {
    return unknown(e);
  }
}

// --- printing -----------------------------------------------------------------------------------------------------------------

export interface PrintResult {
  ok: boolean;
  /** Why it did not print, in staff language (e.g. "Printer is out of paper"). */
  message?: string;
}

/** Characters per line for a paper width; 'big' rows use Pine Labs' 24-character (double size) font. */
export const lineWidth = (paperMm: 58 | 80) => (paperMm === 58 ? 32 : 48);

/** Our print rows as PrintData lines. Rows are already wrapped by the server to fit. */
export function printDataLines(page: PrintablePage) {
  const width = lineWidth(page.paper_mm);
  return page.rows.map((row) => ({
    PrintDataType: '0',
    PrinterWidth: row.style === 'big' ? 24 : width,
    IsCenterAligned: row.align === 'center',
    DataToPrint: row.text,
    ImagePath: '0',
    ImageData: '0',
    textStyle: row.style === 'normal' ? 0 : 1,
  }));
}

/** Pine Labs' PrintRefNo allows 10 characters: keep the distinctive end of the KOT or bill number. */
export const printRef = (reference: string) => reference.replace(/[^A-Za-z0-9]/g, '').slice(-10) || 'PRINT';

export async function print(transport: Transport, ctx: Context, page: PrintablePage): Promise<PrintResult> {
  try {
    const answer = await call(transport, ctx, METHOD.PRINT_DATA, {
      PrintRefNo: printRef(page.reference),
      SavePrintData: false,
      Data: printDataLines(page),
    });
    if (answer.ok) return { ok: true };
    return { ok: false, message: PRINTER_ERRORS[answer.code] ?? (answer.message || `Printer error ${answer.code}`) };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : 'The printer did not answer' };
  }
}

// --- day end and device info ------------------------------------------------------------------------------------------------

export interface SettlementLine {
  bank: string;
  terminalId: string;
  creditCount: number;
  creditPaise: number;
  debitCount: number;
  debitPaise: number;
}

export async function settle(transport: Transport, ctx: Context): Promise<{ ok: boolean; message: string; lines: SettlementLine[] }> {
  try {
    const answer = await call(transport, ctx, METHOD.SETTLEMENT);
    const summary = (answer.detail.SettlementSummary as any[]) ?? [];
    return {
      ok: answer.ok,
      message: answer.message || (answer.ok ? 'Settled' : `Settlement failed (${answer.code})`),
      lines: summary.map((s) => ({
        bank: String(s.BatchName ?? s.AcquirerCode ?? ''),
        terminalId: String(s.TID ?? ''),
        creditCount: Number(s.CreditCount ?? 0),
        creditPaise: Number(s.CreditAmount ?? 0),
        debitCount: Number(s.DebitCount ?? 0),
        debitPaise: Number(s.DebitAmount ?? 0),
      })),
    };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : 'The terminal did not answer', lines: [] };
  }
}

export async function terminalInfo(transport: Transport, ctx: Context): Promise<Record<string, string>> {
  const answer = await call(transport, ctx, METHOD.TERMINAL_INFO);
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(answer.detail)) if (typeof value !== 'object') out[key] = String(value);
  return out;
}
