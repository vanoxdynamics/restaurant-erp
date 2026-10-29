/**
 * Restaurant ERP design tokens — light theme.
 *
 * Neutral, crisp and dependable: near-white surfaces, hairline borders, near-black text and
 * one deep green for actions. Status colors are clear but not alarming, so a full floor plan
 * stays readable during a rush. Type is Inter, chosen for legibility at small sizes.
 */

/** Inter faces loaded by the app (see `fontAssets`). Use these instead of fontWeight. */
export const fonts = {
  regular: 'Inter_400Regular',
  medium: 'Inter_500Medium',
  semibold: 'Inter_600SemiBold',
  bold: 'Inter_700Bold',
};

export const colors = {
  // Brand: deep green for primary actions and selection
  primary: '#146c54',
  primaryLight: '#1d8a6c',
  primaryDark: '#0f5040',
  primaryGlow: '#e6f2ee',

  // Secondary accent for highlights (best sellers, featured)
  accent: '#c26b1a',
  accentLight: '#e0a465',
  accentDark: '#9a520f',
  accentBg: '#fbf0e4',

  // Legacy "cyan" slot follows the brand so every active state looks the same
  cyan: '#146c54',
  cyanDark: '#0f5040',

  // Table / service states
  available: '#1f8a4c',
  availableBg: '#e7f5ec',
  occupied: '#c2410c',
  occupiedBg: '#fdeee6',
  billed: '#b45309',
  billedBg: '#fdf3e2',
  dirty: '#6d4fc2',
  dirtyBg: '#f0ecfb',

  // Surfaces
  background: '#f5f6f8',
  backgroundSecondary: '#ffffff',
  surface: '#ffffff',
  surfaceLight: '#f0f2f5',
  surfaceBorder: '#e1e5ea',
  surfaceBorderLight: '#eceff3',

  // Text
  text: '#111827',
  textSecondary: '#4b5563',
  textMuted: '#6b7280',
  textInverse: '#ffffff',

  // Semantic
  success: '#1f8a4c',
  successBg: '#e7f5ec',
  warning: '#b45309',
  warningBg: '#fdf3e2',
  danger: '#c62828',
  dangerBg: '#fdecec',
  info: '#146c54',

  // 86 / sold out
  outOfStock: '#c62828',
  outOfStockBg: '#fdecec',

  overlay: 'rgba(17, 24, 39, 0.45)',
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24,
  xxxl: 32,
};

export const radii = {
  xs: 4,
  sm: 6,
  md: 10,
  lg: 12,
  xl: 16,
  full: 9999,
};

export const typography = {
  display: {
    fontFamily: fonts.bold,
    fontSize: 34,
    lineHeight: 40,
    letterSpacing: -0.8,
  },
  h1: {
    fontFamily: fonts.bold,
    fontSize: 26,
    lineHeight: 32,
    letterSpacing: -0.5,
  },
  h2: {
    fontFamily: fonts.bold,
    fontSize: 20,
    lineHeight: 26,
    letterSpacing: -0.3,
  },
  h3: {
    fontFamily: fonts.semibold,
    fontSize: 17,
    lineHeight: 24,
    letterSpacing: -0.2,
  },
  body: {
    fontFamily: fonts.regular,
    fontSize: 16,
    lineHeight: 24,
  },
  bodyBold: {
    fontFamily: fonts.semibold,
    fontSize: 16,
    lineHeight: 24,
  },
  caption: {
    fontFamily: fonts.medium,
    fontSize: 14,
    lineHeight: 20,
  },
  label: {
    fontFamily: fonts.semibold,
    fontSize: 13,
    lineHeight: 18,
  },
  code: {
    fontFamily: fonts.medium,
    fontSize: 14,
    letterSpacing: 0.2,
  },
};

/** Subtle elevation; cards also carry a hairline border so edges stay crisp. */
export const shadows = {
  card: {
    shadowColor: '#0f172a',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 3,
    elevation: 1,
  },
  raised: {
    shadowColor: '#0f172a',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.14,
    shadowRadius: 24,
    elevation: 8,
  },
};

/** Minimum comfortable touch target (Apple HIG / Material). */
export const touchTarget = 48;

export const theme = {
  colors,
  fonts,
  spacing,
  radii,
  typography,
  shadows,
};

export type Theme = typeof theme;

/** Map a numeric/keyword fontWeight to the matching Inter face. */
export function fontForWeight(weight?: string | number): string {
  const w = String(weight ?? '400');
  if (w === 'bold' || Number(w) >= 700) return fonts.bold;
  if (Number(w) >= 600) return fonts.semibold;
  if (Number(w) >= 500) return fonts.medium;
  return fonts.regular;
}

/**
 * Turn shouty status codes into readable labels: "OCCUPIED" -> "Occupied",
 * "PARTIALLY_PAID" -> "Partially paid". Mixed-case text is left alone.
 */
export function humanizeLabel(label: string): string {
  if (!label || label !== label.toUpperCase() || !/[A-Z]/.test(label)) return label;
  const words = label.replace(/_/g, ' ').toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
