import React, { useState } from 'react';
import { RosterEntry } from '@repo/api-client';
import { PinScreen } from './PinScreen';
import { StaffPickerScreen } from './StaffPickerScreen';

/** Sign-in: choose who is working, then enter that person's PIN. Locking returns here. */
export const SignInScreen: React.FC = () => {
  const [staff, setStaff] = useState<RosterEntry | null>(null);
  if (staff) return <PinScreen staff={staff} onBack={() => setStaff(null)} />;
  return <StaffPickerScreen onSelect={setStaff} />;
};
