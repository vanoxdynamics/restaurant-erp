/**
 * GST & Billing Math Utilities
 * Supports Indian GST rules:
 * - 5% Restaurant GST split equally into 2.5% CGST + 2.5% SGST
 * - Inclusive vs. Exclusive calculation
 * - Service charge & discount math
 */

export interface GSTCalculationResult {
  basePaise: number;
  totalTaxPaise: number;
  cgstPaise: number;
  sgstPaise: number;
  totalWithTaxPaise: number;
}

/**
 * Calculates CGST and SGST splits for a given amount and tax rate.
 * @param amountPaise The amount in paise
 * @param taxRatePercentage GST percentage (e.g., 5 for 5%)
 * @param isInclusive Whether the input amount already includes GST
 */
export function calculateGST(
  amountPaise: number,
  taxRatePercentage: number = 5,
  isInclusive: boolean = false
): GSTCalculationResult {
  if (amountPaise <= 0 || taxRatePercentage <= 0) {
    return {
      basePaise: amountPaise,
      totalTaxPaise: 0,
      cgstPaise: 0,
      sgstPaise: 0,
      totalWithTaxPaise: amountPaise,
    };
  }

  let basePaise: number;
  let totalTaxPaise: number;

  if (isInclusive) {
    // base = amount / (1 + rate / 100)
    basePaise = Math.round(amountPaise / (1 + taxRatePercentage / 100));
    totalTaxPaise = amountPaise - basePaise;
  } else {
    basePaise = amountPaise;
    totalTaxPaise = Math.round(amountPaise * (taxRatePercentage / 100));
  }

  // Split equally into CGST and SGST (half and half)
  const cgstPaise = Math.round(totalTaxPaise / 2);
  const sgstPaise = totalTaxPaise - cgstPaise;

  const totalWithTaxPaise = basePaise + totalTaxPaise;

  return {
    basePaise,
    totalTaxPaise,
    cgstPaise,
    sgstPaise,
    totalWithTaxPaise,
  };
}

/**
 * Calculates service charge in paise
 */
export function calculateServiceCharge(
  subtotalPaise: number,
  ratePercentage: number = 0
): number {
  if (subtotalPaise <= 0 || ratePercentage <= 0) return 0;
  return Math.round(subtotalPaise * (ratePercentage / 100));
}

/**
 * Calculates discount amount in paise with optional maximum cap
 */
export function calculateDiscount(
  subtotalPaise: number,
  type: 'FLAT' | 'PERCENTAGE',
  value: number,
  maxCapPaise?: number
): number {
  if (subtotalPaise <= 0 || value <= 0) return 0;

  let discountPaise = 0;
  if (type === 'FLAT') {
    discountPaise = Math.min(value, subtotalPaise);
  } else {
    discountPaise = Math.round(subtotalPaise * (value / 100));
  }

  if (maxCapPaise && maxCapPaise > 0) {
    discountPaise = Math.min(discountPaise, maxCapPaise);
  }

  return Math.min(discountPaise, subtotalPaise);
}

/**
 * Calculates total bill breakdown
 */
export interface BillBreakdown {
  subtotalPaise: number;
  discountPaise: number;
  taxablePaise: number;
  cgstPaise: number;
  sgstPaise: number;
  totalTaxPaise: number;
  serviceChargePaise: number;
  grandTotalPaise: number;
  roundOffPaise: number;
}

export function computeBillBreakdown(params: {
  subtotalPaise: number;
  discountPaise?: number;
  taxRatePercent?: number;
  isTaxInclusive?: boolean;
  serviceChargePercent?: number;
}): BillBreakdown {
  const {
    subtotalPaise,
    discountPaise = 0,
    taxRatePercent = 5,
    isTaxInclusive = false,
    serviceChargePercent = 0,
  } = params;

  const netAfterDiscount = Math.max(0, subtotalPaise - discountPaise);
  const serviceChargePaise = calculateServiceCharge(netAfterDiscount, serviceChargePercent);

  const gst = calculateGST(netAfterDiscount, taxRatePercent, isTaxInclusive);

  const rawGrandTotal = gst.totalWithTaxPaise + serviceChargePaise;
  // Round off to nearest rupee (nearest 100 paise)
  const roundedGrandTotal = Math.round(rawGrandTotal / 100) * 100;
  const roundOffPaise = roundedGrandTotal - rawGrandTotal;

  return {
    subtotalPaise,
    discountPaise,
    taxablePaise: gst.basePaise,
    cgstPaise: gst.cgstPaise,
    sgstPaise: gst.sgstPaise,
    totalTaxPaise: gst.totalTaxPaise,
    serviceChargePaise,
    grandTotalPaise: roundedGrandTotal,
    roundOffPaise,
  };
}
