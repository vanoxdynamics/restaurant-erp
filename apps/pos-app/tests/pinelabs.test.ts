import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as pl from '../src/devices/pinelabs.ts';

const ctx = { applicationId: 'APP123', userId: 'sana.waiter' };

/** A transport that records requests and replies with a fixed answer (or fails). */
function fake(reply: unknown | Error) {
  const sent: any[] = [];
  const transport = async (json: string) => {
    sent.push(JSON.parse(json));
    if (reply instanceof Error) throw reply;
    return JSON.stringify(reply);
  };
  return { sent, transport };
}

const ok = (detail: Record<string, unknown>) => ({ Response: { ResponseCode: '0', ResponseMsg: 'APPROVED' }, Detail: detail });

test('requests carry the header Pine Labs requires', () => {
  const req = JSON.parse(pl.buildRequest(ctx, pl.METHOD.TERMINAL_INFO));
  assert.deepEqual(req, { Header: { ApplicationId: 'APP123', UserId: 'sana.waiter', MethodId: '1004', VersionNo: '1.0' } });
});

test('a card sale sends the amount in paise, the short reference and the sale type', async () => {
  const { sent, transport } = fake(ok({ ApprovalCode: 'A1B2C3', InvoiceNumber: 12, CardNumber: '************1111', CardholderName: 'ASHA RAO' }));
  const result = await pl.sale(transport, ctx, 'card', 9999.6, 'ABCDEFGHJKLM');
  assert.equal(sent[0].Header.MethodId, '1001');
  assert.deepEqual(sent[0].Detail, { TransactionType: 4001, BillingRefNo: 'ABCDEFGHJK', PaymentAmount: 10000, IsSwipe: true });
  assert.equal(result.status, 'approved');
  assert.equal(result.approvalCode, 'A1B2C3');
  assert.equal(result.details.InvoiceNumber, 12);
  assert.equal('CardholderName' in result.details, false); // not kept
});

test('UPI uses the UPI sale and void types (not the example in their guide)', async () => {
  const a = fake(ok({ ApprovalCode: 'U1' }));
  await pl.sale(a.transport, ctx, 'upi', 5000, 'REF1');
  assert.equal(a.sent[0].Detail.TransactionType, 5120);
  const b = fake(ok({ ApprovalCode: 'V1' }));
  await pl.voidSale(b.transport, ctx, 'upi', 5000, 'REF1', '000012', '01');
  assert.deepEqual(b.sent[0].Detail, { TransactionType: 5121, BillingRefNo: 'REF1', PaymentAmount: 5000, InvoiceNo: '000012', BankCode: '01', IsSwipe: true });
  const c = fake(ok({ ApprovalCode: 'V2' }));
  await pl.voidSale(c.transport, ctx, 'card', 5000, 'REF1', '000012', '01');
  assert.equal(c.sent[0].Detail.TransactionType, 4006);
});

test('success needs response code 0 and an approval code', () => {
  const answer = (code: string, detail: Record<string, unknown>, msg = '') => pl.readAnswer(JSON.stringify({ Response: { ResponseCode: code, ResponseMsg: msg }, Detail: detail }));
  assert.equal(pl.readTransaction(answer('0', { ApprovalCode: 'X1' })).status, 'approved');
  assert.equal(pl.readTransaction(answer('0', { ApprovalCode: '' })).status, 'declined');
  assert.equal(pl.readTransaction(answer('1', { ApprovalCode: 'X1' }, 'DECLINED')).status, 'declined');
  const pending = pl.readTransaction(answer('100', {}, 'TRANSACTION INITIATED CHECK GET STATUS'));
  assert.equal(pending.status, 'unknown'); // code 100: ask Get Status
});

test('no answer from the terminal means the result is unknown, never declined', async () => {
  const { transport } = fake(new Error('Plutus app closed'));
  const result = await pl.sale(transport, ctx, 'card', 100, 'REF');
  assert.equal(result.status, 'unknown');
  assert.match(result.message, /Plutus app closed/);
  const unreadable = await pl.sale(async () => 'not json', ctx, 'card', 100, 'REF');
  assert.equal(unreadable.status, 'unknown');
});

test('get status asks with the same reference and amount', async () => {
  const { sent, transport } = fake(ok({ ApprovalCode: 'S1' }));
  const result = await pl.status(transport, ctx, 2500, 'REF9');
  assert.deepEqual(sent[0].Detail, { TransactionType: 5122, BillingRefNo: 'REF9', PaymentAmount: 2500, IsSwipe: true });
  assert.equal(result.status, 'approved');
});

test('print rows become PrintData lines for the paper width', async () => {
  const page = {
    reference: 'KOT-BLR1-20261104-0019', paper_mm: 58 as const, text: '',
    rows: [
      { style: 'big' as const, align: 'center' as const, text: 'KITCHEN' },
      { style: 'bold' as const, align: 'left' as const, text: '2 x Naan' },
      { style: 'normal' as const, align: 'left' as const, text: '  - less butter' },
    ],
  };
  const lines = pl.printDataLines(page);
  assert.deepEqual(lines.map((l) => [l.PrinterWidth, l.IsCenterAligned, l.textStyle, l.DataToPrint]), [
    [24, true, 1, 'KITCHEN'],
    [32, false, 1, '2 x Naan'],
    [32, false, 0, '  - less butter'],
  ]);
  assert.equal(pl.lineWidth(80), 48);
  const { sent, transport } = fake({ Response: { ResponseCode: '0', ResponseMsg: 'SUCCESS' } });
  assert.deepEqual(await pl.print(transport, ctx, page), { ok: true });
  assert.equal(sent[0].Header.MethodId, '1002');
  assert.equal(sent[0].Detail.PrintRefNo, '202611040019'.slice(-10)); // at most 10 characters
  assert.equal(sent[0].Detail.SavePrintData, false);
});

test('printer errors come back in words staff understand', async () => {
  const page = { reference: 'X', paper_mm: 80 as const, text: '', rows: [] };
  const out = await pl.print(fake({ Response: { ResponseCode: '1002', ResponseMsg: 'PRINTER IS OUT OF PAPER' } }).transport, ctx, page);
  assert.deepEqual(out, { ok: false, message: 'Printer is out of paper' });
  const jam = await pl.print(fake({ Response: { ResponseCode: '1009' } }).transport, ctx, page);
  assert.equal(jam.message, 'Paper is jammed');
  const dead = await pl.print(fake(new Error('No printer service')).transport, ctx, page);
  assert.equal(dead.ok, false);
});

test('settlement summary is read per bank', async () => {
  const { transport } = fake({
    Response: { ResponseCode: '0', ResponseMsg: 'Success' },
    Detail: { SettlementSummary: [{ BatchName: 'HDFC', TID: '01000234', CreditCount: 1, CreditAmount: 2100, DebitCount: 5, DebitAmount: 324000 }] },
  });
  const result = await pl.settle(transport, ctx);
  assert.equal(result.ok, true);
  assert.deepEqual(result.lines, [{ bank: 'HDFC', terminalId: '01000234', creditCount: 1, creditPaise: 2100, debitCount: 5, debitPaise: 324000 }]);
});
