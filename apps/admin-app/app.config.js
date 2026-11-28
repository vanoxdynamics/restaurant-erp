/**
 * The owner and manager app, for Android and iOS. The native projects (android/, ios/) are generated from this file and
 * app.json by `expo prebuild`; they are not committed (see docs/pos-devices/03-android-builds.md for the same setup in the POS app).
 *
 * Build-time settings:
 *   APP_VERSION_CODE   Android versionCode and iOS buildNumber; raise it for every store upload
 *   EXPO_PUBLIC_API_URL the API the app talks to
 */
const APP_ID = 'com.vanoxdynamics.admin';

module.exports = ({ config }) => {
  const build = String(Number(process.env.APP_VERSION_CODE || 1));

  return {
    ...config,
    scheme: 'vanoxadmin',
    android: {
      ...config.android,
      package: APP_ID,
      versionCode: Number(build),
    },
    ios: {
      ...config.ios,
      bundleIdentifier: APP_ID,
      buildNumber: build,
      infoPlist: {
        ...config.ios?.infoPlist,
        // The app uses only standard HTTPS; this skips the export-compliance question on every TestFlight upload.
        ITSAppUsesNonExemptEncryption: false,
      },
    },
  };
};
