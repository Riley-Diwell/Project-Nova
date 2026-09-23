package com.example.novav2

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.SpeechRecognizer
import androidx.activity.ComponentActivity
import androidx.core.content.ContextCompat
import com.example.novav2.service.AssistVoiceService
import com.example.novav2.state.AppForegroundState

/**
 * The actual target Settings > Apps > Default apps > Digital assistant app registers (see the
 * ACTION_ASSIST filter in AndroidManifest). Theme.Transparent + noHistory mean this never draws
 * a visible frame - it exists only to decide, before anything could be shown, how the
 * power-button-hold gesture should be handled:
 *
 * - Nova's own UI is already on screen (AppForegroundState.isInForeground()): hand the gesture
 *   to VoiceScreen's existing mic button flow via EXTRA_AUTO_LISTEN instead of standing up
 *   AssistVoiceService's independent headless listen session - without this, holding the power
 *   button while already inside Nova used to spin up a second SpeechRecognizer/TTS/notification
 *   stack on top of the one already visible, including its own "Nova is listening…" status
 *   notification even though the app itself was right there.
 * - Otherwise, go straight to AssistVoiceService's headless listen-respond round trip if mic
 *   permission and speech recognition are available.
 * - Failing that, fall back to opening MainActivity, because there's no other way to run a
 *   RECORD_AUDIO permission prompt or tell the user speech recognition isn't available here.
 */
class AssistTrampolineActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (AppForegroundState.isInForeground()) {
            startActivity(
                Intent(this, MainActivity::class.java).apply {
                    action = Intent.ACTION_ASSIST
                    putExtra(EXTRA_AUTO_LISTEN, true)
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
            )
        } else {
            val hasMicPermission = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
            if (hasMicPermission && SpeechRecognizer.isRecognitionAvailable(this)) {
                ContextCompat.startForegroundService(this, Intent(this, AssistVoiceService::class.java))
            } else {
                startActivity(
                    Intent(this, MainActivity::class.java).apply {
                        action = Intent.ACTION_ASSIST
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                )
            }
        }

        finish()
        @Suppress("DEPRECATION")
        overridePendingTransition(0, 0)
    }

    companion object {
        /** Boolean extra on the MainActivity fallback intent: set only when the foreground
         * branch above picked this route, so MainActivity/VoiceScreen know to auto-trigger the
         * mic (same as a manual tap) instead of just navigating to the Voice tab and waiting. */
        const val EXTRA_AUTO_LISTEN = "com.example.novav2.extra.AUTO_LISTEN"
    }
}
