/**
 * One code base, several Android apps (docs/pos-devices/01-counter-app-plan.md). EXPO_PUBLIC_APP_VARIANT picks the build:
 *
 *   waiter       staff phones, Play Store                  com.vanoxdynamics.pos
 *   counter      the Pine Labs device, production          com.vanoxdynamics.pos.counter
 *   counter-uat  the Pine Labs device, Pine Labs UAT       com.vanoxdynamics.pos.counter.uat
 *   retail       cafés and shops: quick sale at the counter com.vanoxdynamics.retail
 *
 * The same variable tells the app at runtime what it is (src/device.ts), so the package and the behaviour cannot disagree.
 * Unset, it is `counter` (the browser preview and local development).
 */
const VARIANTS = {
  waiter: { name: 'Restaurant POS', package: 'com.vanoxdynamics.pos' },
  counter: { name: 'Restaurant Counter', package: 'com.vanoxdynamics.pos.counter' },
  'counter-uat': { name: 'Counter UAT', package: 'com.vanoxdynamics.pos.counter.uat' },
  retail: { name: 'Vanox Retail', package: 'com.vanoxdynamics.retail' },
};

module.exports = ({ config }) => {
  const key = process.env.EXPO_PUBLIC_APP_VARIANT || 'counter';
  const variant = VARIANTS[key];
  if (!variant) throw new Error(`Unknown EXPO_PUBLIC_APP_VARIANT "${key}"; use one of ${Object.keys(VARIANTS).join(', ')}`);

  return {
    ...config,
    name: variant.name,
    // Handhelds and phones are used upright.
    orientation: 'portrait',
    android: {
      ...config.android,
      package: variant.package,
      versionCode: Number(process.env.APP_VERSION_CODE || 1),
    },
    ios: { ...config.ios, bundleIdentifier: variant.package },
    extra: { ...config.extra, variant: key },
  };
};
