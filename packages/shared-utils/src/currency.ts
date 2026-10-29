/**
 * Currency utilities: Paise <-> Rupees Conversion and Formatting
 * In restaurant-erp, monetary values in database and network payloads are stored in paise (integers)
 * to avoid floating-point inaccuracies.
 */

/**
 * Converts paise (integer) to rupees (float)
 */
export function paiseToRupees(paise: number): number {
  if (isNaN(paise) || !isFinite(paise)) return 0;
  return paise / 100;
}

/**
 * Converts rupees (float or number) to paise (integer) rounded safely
 */
export function rupeesToPaise(rupees: number): number {
  if (isNaN(rupees) || !isFinite(rupees)) return 0;
  return Math.round(rupees * 100);
}

/**
 * Formats paise to Indian Rupee representation (e.g., ₹1,450.00 or ₹1,450)
 */
export function formatPaiseToRupees(
  paise: number,
  options?: {
    showSymbol?: boolean;
    compact?: boolean;
    decimals?: number;
  }
): string {
  const { showSymbol = true, compact = false, decimals = 2 } = options || {};
  const rupees = paiseToRupees(paise);

  const formatted = new Intl.NumberFormat('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    notation: compact ? 'compact' : 'standard',
  }).format(rupees);

  return showSymbol ? `₹${formatted}` : formatted;
}

/**
 * Generic format currency helper
 */
export function formatCurrency(amount: number, isPaise: boolean = true): string {
  const paise = isPaise ? amount : rupeesToPaise(amount);
  return formatPaiseToRupees(paise);
}

/**
 * Format variance with sign and color coding indicator
 */
export function formatVariancePaise(variancePaise: number): {
  formatted: string;
  isShortage: boolean;
  isOverage: boolean;
  isBalanced: boolean;
} {
  const isShortage = variancePaise < 0;
  const isOverage = variancePaise > 0;
  const isBalanced = variancePaise === 0;

  const sign = isShortage ? '-' : isOverage ? '+' : '';
  const absFormatted = formatPaiseToRupees(Math.abs(variancePaise));

  return {
    formatted: isBalanced ? '₹0.00' : `${sign}${absFormatted}`,
    isShortage,
    isOverage,
    isBalanced,
  };
}
