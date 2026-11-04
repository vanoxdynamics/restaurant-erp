import { create } from 'zustand';
import { defaultPaperMm } from '../device';
import { storage } from './storage';

const PAPER_KEY = 'restaurant_pos_paper_mm';

interface PrinterSettings {
  paperMm: 58 | 80;
  load: () => Promise<void>;
  setPaperMm: (mm: 58 | 80) => Promise<void>;
}

/** This device's paper width, kept across restarts. */
export const usePrinterSettings = create<PrinterSettings>((set) => ({
  paperMm: defaultPaperMm(),
  load: async () => {
    const stored = await storage.get<number>(PAPER_KEY);
    if (stored === 58 || stored === 80) set({ paperMm: stored });
  },
  setPaperMm: async (mm) => {
    set({ paperMm: mm });
    await storage.set(PAPER_KEY, mm);
  },
}));
