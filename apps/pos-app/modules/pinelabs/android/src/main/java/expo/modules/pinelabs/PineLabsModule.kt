package expo.modules.pinelabs

import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import expo.modules.kotlin.Promise
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/**
 * Talks to Pine Labs' Plutus Smart app on the same device with its documented "hybrid app" transport: the request JSON goes
 * out as the REQUEST_DATA extra of a HYBRID_REQUEST intent started for a result, and the answer comes back as RESPONSE_DATA.
 * Everything else (building requests, reading answers) is in TypeScript, src/devices/pinelabs.ts.
 *
 * One request at a time: Plutus shows its own screen (card, UPI, printing) and answers when it is done.
 */
class PineLabsModule : Module() {
  private var pending: Promise? = null

  override fun definition() = ModuleDefinition {
    Name("PineLabs")

    Function("isAvailable") {
      isPlutusInstalled()
    }

    AsyncFunction("request") { requestJson: String, promise: Promise ->
      val activity = appContext.currentActivity
      if (activity == null) {
        promise.reject("E_NO_ACTIVITY", "The app is not in the foreground", null)
        return@AsyncFunction
      }
      if (pending != null) {
        promise.reject("E_BUSY", "Another Pine Labs request is still in progress", null)
        return@AsyncFunction
      }
      val intent = Intent(HYBRID_ACTION).apply {
        setPackage(PLUTUS_PACKAGE)
        putExtra(REQUEST_EXTRA, requestJson)
      }
      pending = promise
      try {
        activity.startActivityForResult(intent, REQUEST_CODE)
      } catch (e: ActivityNotFoundException) {
        pending = null
        promise.reject("E_NOT_INSTALLED", "Pine Labs' Plutus app is not installed on this device", e)
      } catch (e: SecurityException) {
        pending = null
        promise.reject("E_NOT_ALLOWED", "This app is not allowed to call Pine Labs' Plutus app", e)
      }
    }

    OnActivityResult { _, payload ->
      if (payload.requestCode != REQUEST_CODE) return@OnActivityResult
      val promise = pending ?: return@OnActivityResult
      pending = null
      val response = payload.data?.getStringExtra(RESPONSE_EXTRA)
      if (response == null) {
        // No answer: the caller must treat a payment as "unknown" and check its status, never as declined.
        promise.reject("E_NO_RESPONSE", "Pine Labs returned no answer (result ${payload.resultCode})", null)
      } else {
        promise.resolve(response)
      }
    }
  }

  private fun isPlutusInstalled(): Boolean {
    val context = appContext.reactContext ?: return false
    return try {
      context.packageManager.getPackageInfo(PLUTUS_PACKAGE, 0)
      true
    } catch (e: PackageManager.NameNotFoundException) {
      false
    }
  }

  companion object {
    private const val PLUTUS_PACKAGE = "com.pinelabs.masterapp"
    private const val HYBRID_ACTION = "com.pinelabs.masterapp.HYBRID_REQUEST"
    private const val REQUEST_EXTRA = "REQUEST_DATA"
    private const val RESPONSE_EXTRA = "RESPONSE_DATA"
    private const val REQUEST_CODE = 0x504C // "PL"
  }
}
