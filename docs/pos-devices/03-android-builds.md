# Building the Android apps

The POS app (`apps/pos-app`) builds into three Android apps; `EXPO_PUBLIC_APP_VARIANT` picks one (see `app.config.js`):

| Variant | App | Package | Where it goes |
|---|---|---|---|
| `waiter` | Restaurant POS | `com.vanoxdynamics.pos` | Play Store (staff phones) |
| `counter-uat` | Counter UAT | `com.vanoxdynamics.pos.counter.uat` | Pine Labs UAT |
| `counter` | Restaurant Counter | `com.vanoxdynamics.pos.counter` | Pine Labs app store / MDM |

Other build-time settings (baked into the app when it is built):

| Variable | Meaning |
|---|---|
| `EXPO_PUBLIC_API_URL` | the API the app talks to, e.g. the UAT server's HTTPS address |
| `EXPO_PUBLIC_PINELABS_APP_ID` | the ApplicationId Pine Labs issues for our app (UAT and production differ) |
| `APP_VERSION_CODE` | Android version code; raise it for every release |
| `EXPO_PUBLIC_PINELABS_SIMULATOR=1` | development only: fake terminal and printer in the browser or an emulator; never in a release |

## Option A: EAS Build (recommended)

Builds run on Expo's Linux machines, which avoids the Windows problems below, and EAS keeps the signing keys.

```bash
cd apps/pos-app
```

```bash
npx eas-cli login
```

Set `EXPO_PUBLIC_API_URL` and `EXPO_PUBLIC_PINELABS_APP_ID` as EAS environment variables (environment `preview` for UAT,
`production` for live), then:

```bash
npx eas-cli build --platform android --profile counter-uat
```

Profiles are in `eas.json`: `counter-uat`, `counter-production`, `waiter-preview`, `waiter-production`.

## Option B: local build on Windows

Needs Android Studio (its bundled JDK and the Android SDK) and **Windows long paths enabled**. Without long paths, Expo's C++
code fails with `ninja: error: manifest 'build.ninja' still dirty after 100 tries`, because pnpm's dependency folders plus CMake's
object paths exceed 260 characters. Enabling long paths is a one-time machine setting, done by an administrator:

```powershell
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

Restart Windows afterwards. If the error remains, install a newer CMake in Android Studio (SDK Manager > SDK Tools > CMake, 3.31 or
later); the SDK currently has only 3.22.1.

Then generate the native project (it is not committed; it is rebuilt from `app.config.js`):

```bash
cd apps/pos-app
```

```bash
EXPO_PUBLIC_APP_VARIANT=counter-uat npx expo prebuild --platform android --clean
```

And build the signed APK (PowerShell, with the UAT key below):

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"; $env:EXPO_PUBLIC_APP_VARIANT = "counter-uat"; cd android; .\gradlew.bat assembleRelease "-Pandroid.injected.signing.store.file=$env:USERPROFILE\.vanoxdynamics\signing\counter-uat.jks" "-Pandroid.injected.signing.store.password=<from counter-uat.properties>" "-Pandroid.injected.signing.key.alias=counter-uat" "-Pandroid.injected.signing.key.password=<from counter-uat.properties>"
```

The APK is `android/app/build/outputs/apk/release/app-release.apk`. Pine Labs devices are ARM; build `arm64-v8a` and, if Pine
Labs confirms older devices, `armeabi-v7a` too (`-PreactNativeArchitectures=arm64-v8a,armeabi-v7a`).

Status on the development machine (2026-11-04): the Pine Labs module compiles (`:pinelabs:compileReleaseKotlin` succeeds); the full
APK stops at `expo-modules-core`'s C++ step because long paths are off.

## Signing keys

| Key | Where | Fingerprint (SHA-256) |
|---|---|---|
| Counter UAT | `%USERPROFILE%\.vanoxdynamics\signing\counter-uat.jks`, password in `counter-uat.properties` next to it | `C8:E6:FC:CE:71:7A:DD:A2:56:6F:9F:B9:90:B2:F0:49:30:34:EC:C4:9C:78:D8:A9:A9:A7:6C:33:41:2B:D5:8C` |
| Counter production | not created yet: create it with the company's own process (or let EAS manage it) | |
| Waiter (Play Store) | not created yet: Play App Signing holds the release key; you keep only an upload key | |

Keys are never committed (`.gitignore` excludes `*.jks`, `*.keystore` and the generated `android/` folder). Back the UAT key up;
**losing the production key means the app can never be updated on devices that have it**.
