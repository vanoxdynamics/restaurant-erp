const { getDefaultConfig } = require('expo/metro-config');
const path = require('path');
const os = require('os');

// Automatically detect the active physical LAN IPv4 address (prioritizing Wi-Fi,
// ignoring VirtualBox, WSL, Hyper-V, and other virtual host-only adapters)
function getActiveLanIp() {
  if (process.env.REACT_NATIVE_PACKAGER_HOSTNAME) {
    return process.env.REACT_NATIVE_PACKAGER_HOSTNAME;
  }
  const interfaces = os.networkInterfaces();
  let candidate = null;
  for (const name of Object.keys(interfaces)) {
    const isVirtual =
      /virtual|vbox|wsl|hyper-v|vethernet|pseudo|bluetooth/i.test(name) ||
      (interfaces[name] || []).some((i) => i.address.startsWith('192.168.56.'));

    for (const iface of interfaces[name] || []) {
      if (iface.family === 'IPv4' && !iface.internal && !isVirtual) {
        if (/wi-?fi|wlan|wireless/i.test(name)) {
          return iface.address; // Direct match for Wi-Fi interface
        }
        if (!candidate) candidate = iface.address;
      }
    }
  }
  return candidate;
}

const detectedIp = getActiveLanIp();
if (detectedIp && !process.env.REACT_NATIVE_PACKAGER_HOSTNAME) {
  process.env.REACT_NATIVE_PACKAGER_HOSTNAME = detectedIp;
  console.log(`[metro.config] Configured REACT_NATIVE_PACKAGER_HOSTNAME = ${detectedIp}`);
}

const projectRoot = __dirname;
const monorepoRoot = path.resolve(projectRoot, '../..');

const config = getDefaultConfig(projectRoot);

// 1. Watch all files within the monorepo
config.watchFolders = [monorepoRoot];

// 2. Let Metro know where to resolve packages and in what order
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, 'node_modules'),
  path.resolve(monorepoRoot, 'node_modules'),
];

// 3. Force singleton packages across the monorepo (@tanstack/react-query, react, react-native, safe-area-context)
// to resolve strictly from admin-app so React contexts are never duplicated.
config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (
    moduleName === '@tanstack/react-query' ||
    moduleName.startsWith('@tanstack/react-query/') ||
    moduleName === 'react' ||
    moduleName.startsWith('react/') ||
    moduleName === 'react-native' ||
    moduleName.startsWith('react-native/') ||
    // Shared UI reads the app's safe-area insets: one copy, or its context would be empty.
    moduleName === 'react-native-safe-area-context'
  ) {
    const redirectContext = {
      ...context,
      originModulePath: path.resolve(projectRoot, 'index.js'),
    };
    return context.resolveRequest(redirectContext, moduleName, platform);
  }
  return context.resolveRequest(context, moduleName, platform);
};

module.exports = config;
