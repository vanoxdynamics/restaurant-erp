import React, { useState } from 'react';
import { DayEndScreen } from '../screens/DayEndScreen';
import { KotPrinterScreen } from '../screens/KotPrinterScreen';
import { PayScreen } from './PayScreen';
import { SaleScreen } from './SaleScreen';

type ApparelRoute = { name: 'sale' } | { name: 'pay'; saleId: string } | { name: 'printer' } | { name: 'dayend' };

/**
 * The dress shop counter: sell, pay, printer settings and day end. The cash day and printer screens are the shared ones.
 * Returns and exchanges arrive in stage A5, the day report in A6.
 */
export function ApparelApp(): React.JSX.Element {
  const [route, setRoute] = useState<ApparelRoute>({ name: 'sale' });
  const sale = () => setRoute({ name: 'sale' });

  switch (route.name) {
    case 'pay':
      return <PayScreen saleId={route.saleId} onBack={sale} onDone={sale} />;
    case 'printer':
      return <KotPrinterScreen onBack={sale} />;
    case 'dayend':
      return <DayEndScreen onBack={sale} />;
    default:
      return (
        <SaleScreen
          onSale={(saleId) => setRoute({ name: 'pay', saleId })}
          onPrinter={() => setRoute({ name: 'printer' })}
          onDayEnd={() => setRoute({ name: 'dayend' })}
        />
      );
  }
}
