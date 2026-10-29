/**
 * Formatting and utility helpers for Restaurant ERP
 */

import { WastageReason } from './enums';

/**
 * Formats seconds into human-readable duration (e.g., "42m", "1h 15m")
 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours}h ${remainingMinutes}m`;
}

/**
 * Formats timestamp into occupancy elapsed time from start time
 */
export function getOccupancyTime(startTime: string | Date | number): string {
  const startMs = typeof startTime === 'string' ? new Date(startTime).getTime() : typeof startTime === 'number' ? startTime : startTime.getTime();
  const diffSec = Math.floor((Date.now() - startMs) / 1000);
  return formatDuration(diffSec);
}

/**
 * Formats date into readable format: "20 Oct 2026, 01:45 PM"
 */
export function formatDateTime(dateInput: string | Date): string {
  const date = typeof dateInput === 'string' ? new Date(dateInput) : dateInput;
  return new Intl.DateTimeFormat('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  }).format(date);
}

/**
 * Formats date to simple time: "01:45 PM"
 */
export function formatTime(dateInput: string | Date): string {
  const date = typeof dateInput === 'string' ? new Date(dateInput) : dateInput;
  return new Intl.DateTimeFormat('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  }).format(date);
}

/**
 * Masks a PIN: '1234' -> '••••'
 */
export function maskPIN(pin: string): string {
  return '•'.repeat(pin.length);
}

const WASTAGE_REASON_LABELS: Record<string, string> = {
  [WastageReason.CUSTOMER_CHANGED_MIND]: 'Customer changed mind',
  [WastageReason.KITCHEN_SPILL]: 'Spillage',
  [WastageReason.WRONG_ORDER_PUNCH]: 'Wrong order',
  [WastageReason.QUALITY_REJECTION]: 'Quality issue',
  [WastageReason.EXCESS_PREPARATION]: 'Over-prepared',
};

/** Display label for a void reason; unknown reasons are shown as entered. */
export function wastageReasonLabel(reason: string): string {
  return WASTAGE_REASON_LABELS[reason] ?? reason;
}
