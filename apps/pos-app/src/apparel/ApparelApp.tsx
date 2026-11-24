import React, { useEffect, useState } from 'react';
import { useApparelCartStore } from './apparelCartStore';
import { DayEndScreen } from '../screens/DayEndScreen';
import { KotPrinterScreen } from '../screens/KotPrinterScreen';
import { DayScreen } from './DayScreen';
import { PayScreen } from './PayScreen';
import { ReturnsScreen } from './ReturnsScreen';
import { SaleScreen } from './SaleScreen';
import { ShiftScreen } from './ShiftScreen';

/** Store credit made by an exchange, waiting to be spent on the next sale. */
export interface PendingCredit {
  code: string;
  amount_paise: number;
}

type ApparelRoute =
  | { name: 'sale' }
  | { name: 'pay'; saleId: string }
  | { name: 'returns' }
  | { name: 'today' }
  | { name: 'shift' }
  | { name: 'printer' }
  | { name: 'dayend' };

/**
 * The dress shop counter: sell, pay, take goods back, printer settings and day end. The cash day and printer screens are the
 * shared ones. An exchange is a return to store credit followed by a sale; the credit is carried from the one to the other here.
 * Managers also see today's report.
 */
export function ApparelApp(): React.JSX.Element {
  const [route, setRoute] = useState<ApparelRoute>({ name: 'sale' });
  const [credit, setCredit] = useState<PendingCredit | null>(null);
  const sale = () => setRoute({ name: 'sale' });

  // The cart (and any invoice waiting for payment) is kept on the device; bring it back after a reload or restart.
  useEffect(() => {
    void useApparelCartStore.getState().load();
  }, []);

  switch (route.name) {
    case 'pay':
      return (
        <PayScreen
          saleId={route.saleId}
          credit={credit}
          onBack={sale}
          onDone={() => {
            // Paid: only now is the cart emptied for the next customer.
            useApparelCartStore.getState().clear();
            setCredit(null);
            sale();
          }}
          onCancelled={() => {
            // The invoice is cancelled but the pieces stay in the cart, to change and invoice again.
            useApparelCartStore.getState().setPendingSale(null);
            sale();
          }}
        />
      );
    case 'returns':
      return (
        <ReturnsScreen
          onBack={sale}
          onExchange={(c) => {
            setCredit(c);
            sale();
          }}
        />
      );
    case 'today':
      return <DayScreen onBack={sale} />;
    case 'shift':
      return <ShiftScreen onBack={sale} />;
    case 'printer':
      return <KotPrinterScreen onBack={sale} />;
    case 'dayend':
      return <DayEndScreen onBack={sale} />;
    default:
      return (
        <SaleScreen
          credit={credit}
          onDropCredit={() => setCredit(null)}
          onSale={(saleId) => setRoute({ name: 'pay', saleId })}
          onReturns={() => setRoute({ name: 'returns' })}
          onToday={() => setRoute({ name: 'today' })}
          onShift={() => setRoute({ name: 'shift' })}
          onPrinter={() => setRoute({ name: 'printer' })}
          onDayEnd={() => setRoute({ name: 'dayend' })}
        />
      );
  }
}
