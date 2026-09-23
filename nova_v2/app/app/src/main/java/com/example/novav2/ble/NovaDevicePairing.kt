package com.example.novav2.ble

import android.bluetooth.le.ScanFilter
import android.companion.AssociationRequest
import android.companion.BluetoothLeDeviceFilter
import android.content.Context
import android.os.Build
import android.os.ParcelUuid
import androidx.annotation.RequiresApi

/**
 * Pairing via CompanionDeviceManager rather than a hand-rolled scan+connect loop —
 * see nova_v2/docs/ble-protocol.md "Exclusive one-device-to-one-phone". CDM gives the
 * system's own device-picker UI (filtered to the Nova Service UUID, so only real Nova
 * devices show up, not every BLE peripheral nearby), persists the association across
 * reboots at the OS level rather than just in our SharedPreferences, and grants
 * background BLE access without needing ACCESS_FINE_LOCATION the way raw
 * BluetoothLeScanner scanning does.
 *
 * The actual `CompanionDeviceManager.associate(...)` call has to happen from an
 * Activity context (its result comes back through an IntentSender an Activity
 * launches), so it lives in [com.example.novav2.ui.screens.DeviceScreen] itself,
 * not here — this object only builds the request and persists the result.
 *
 * A CDM association is a separate thing from BLE bonding: this only records "the
 * OS knows about this device," it does not perform the Security Manager handshake
 * that firmware's JustWorks security expects. [NovaGattClient] is what triggers
 * that, on first connect.
 */
object NovaDevicePairing {
    private const val PREFS_NAME = "nova_device"
    private const val KEY_DEVICE_ADDRESS = "paired_device_address"

    fun pairedDeviceAddress(context: Context): String? =
        prefs(context).getString(KEY_DEVICE_ADDRESS, null)

    fun isPaired(context: Context): Boolean = pairedDeviceAddress(context) != null

    fun savePairedDevice(context: Context, address: String) {
        prefs(context).edit().putString(KEY_DEVICE_ADDRESS, address).apply()
    }

    /** Forgets the paired device app-side only. Does not remove the OS-level CDM
     * association or an existing BLE bond — those need their own explicit teardown
     * (CompanionDeviceManager.disassociate / BluetoothDevice.removeBond), which is a
     * "forget this device" UI action not implemented yet, not something to do
     * silently as a side effect of clearing local prefs. */
    fun clearPairedDevice(context: Context) {
        prefs(context).edit().remove(KEY_DEVICE_ADDRESS).apply()
    }

    /**
     * Filtered to the Nova Service UUID so the system picker only lists actual
     * Nova devices. [AssociationRequest.Builder.setSingleDevice] (true) is half of
     * "only one Nova device" — it stops the picker from letting the user multi-select;
     * the other half is this app refusing to start a new association request at all
     * while [isPaired] is already true (enforced by the caller in DeviceScreen, not
     * here — this function only builds the request).
     */
    @RequiresApi(Build.VERSION_CODES.O)
    fun buildAssociationRequest(): AssociationRequest {
        val filter = BluetoothLeDeviceFilter.Builder()
            .setScanFilter(
                ScanFilter.Builder()
                    .setServiceUuid(ParcelUuid(NovaBleProtocol.SERVICE_UUID))
                    .build()
            )
            .build()

        return AssociationRequest.Builder()
            .addDeviceFilter(filter)
            .setSingleDevice(true)
            .build()
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
