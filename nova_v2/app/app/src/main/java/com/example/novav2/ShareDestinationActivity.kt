package com.example.novav2

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import com.example.novav2.ble.NovaDeviceRepository
import com.example.novav2.state.DeviceCompass
import com.example.novav2.state.SharedPlaceResolver
import kotlinx.coroutines.launch

/**
 * "Share -> Nova compass" from Google Maps: points the device's compass at the shared place
 * ([DeviceCompass.setSharedPlace]). Google Maps offers no way to read where it is navigating to,
 * so sharing the place is how the user tells Nova. Invisible like [AssistTrampolineActivity] - it
 * stays only for the redirect/geocode lookup (a second or so), then says what happened in a toast.
 */
class ShareDestinationActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val text = intent?.takeIf { it.action == Intent.ACTION_SEND }?.getStringExtra(Intent.EXTRA_TEXT)
        if (text.isNullOrBlank()) {
            done(getString(R.string.share_destination_not_a_place))
            return
        }
        lifecycleScope.launch {
            val place = SharedPlaceResolver.resolve(applicationContext, text)
            val message = when {
                place == null -> getString(R.string.share_destination_not_a_place)
                DeviceCompass.isAlreadyThere(place.latitude, place.longitude) ->
                    getString(R.string.share_destination_already_there, place.label ?: getString(R.string.share_destination_there))
                else -> {
                    DeviceCompass.setSharedPlace(applicationContext, place.latitude, place.longitude, place.label)
                    val name = place.label ?: getString(R.string.share_destination_there)
                    if (NovaDeviceRepository.commandSender.value != null) {
                        getString(R.string.share_destination_set, name)
                    } else {
                        getString(R.string.share_destination_set_disconnected, name)
                    }
                }
            }
            done(message)
        }
    }

    private fun done(message: String) {
        Toast.makeText(applicationContext, message, Toast.LENGTH_SHORT).show()
        finish()
        @Suppress("DEPRECATION")
        overridePendingTransition(0, 0)
    }
}
