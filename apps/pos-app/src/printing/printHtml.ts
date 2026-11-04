import * as Print from 'expo-print';

/**
 * Open the system print dialog for a print-ready page (AirPrint on iPad, the Android print service). Only the page is printed;
 * its own CSS sets the paper width, so `widthMm` matters only on web.
 */
export async function printHtml(html: string, _widthMm: 58 | 80 = 80): Promise<void> {
  await Print.printAsync({ html });
}
