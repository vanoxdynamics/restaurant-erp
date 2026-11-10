import React, { useState } from 'react';
import { View } from 'react-native';
import { useBusiness } from '../business';
import { WrongApp } from '../components/WrongApp';
import { BillScreen } from '../screens/BillScreen';
import { DayEndScreen } from '../screens/DayEndScreen';
import { KotPrinterScreen } from '../screens/KotPrinterScreen';
import { OpenSalesScreen } from './OpenSalesScreen';
import { QuickSaleScreen } from './QuickSaleScreen';

type RetailRoute =
  | { name: 'sale' }
  | { name: 'open' }
  | { name: 'bill'; orderId: string; title: string }
  | { name: 'printer' }
  | { name: 'dayend' };

/**
 * The retail app (cafés, grocery, general retail): one counter that sells, bills, takes payment and prints. It has no tables,
 * floor or waiters; those belong to the restaurant apps. Billing, payments, printing and day end are the shared screens.
 */
export function RetailMain(): React.JSX.Element {
  const [route, setRoute] = useState<RetailRoute>({ name: 'sale' });
  const business = useBusiness();
  const sale = () => setRoute({ name: 'sale' });

  if (!business.ready) return <View style={{ flex: 1 }} />;
  if (business.category === 'restaurant') return <WrongApp expected="restaurant" />;

  switch (route.name) {
    case 'bill':
      return <BillScreen orderId={route.orderId} title={route.title} onBack={sale} onDone={sale} />;
    case 'open':
      return (
        <OpenSalesScreen onBack={sale} onOpenBill={(orderId, title) => setRoute({ name: 'bill', orderId, title })} />
      );
    case 'printer':
      return <KotPrinterScreen onBack={sale} />;
    case 'dayend':
      return <DayEndScreen onBack={sale} />;
    default:
      return (
        <QuickSaleScreen
          onBill={(orderId, title) => setRoute({ name: 'bill', orderId, title })}
          onOpenBills={() => setRoute({ name: 'open' })}
          onPrinter={() => setRoute({ name: 'printer' })}
          onDayEnd={() => setRoute({ name: 'dayend' })}
        />
      );
  }
}
