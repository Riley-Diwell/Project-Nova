package com.example.novav2

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.SpeechRecognizer
import androidx.activity.ComponentActivity
import androidx.core.content.ContextCompat
import com.example.novav2.service.AssistVoiceService

/**
 * The actual target Settings > Apps > Default apps > Digital assistant app registers (see the
 * ACTION_ASSIST filter in AndroidManifest). Theme.Transparent + noHistory mean this never draws
 * a visible frame - it exists only to decide, before anything could be shown, whether the
 * power-button-hold gesture can go straight to AssistVoiceService's headless listen-respond
 * round trip, or has to fall back to opening MainActivity because there's no other way to run a
 * RECORD_AUDIO permission prompt or tell the user speech recognition isn't available here.
 */
class AssistTrampolineActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

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

        finish()
        @Suppress("DEPRECATION")
        overridePendingTransition(0, 0)
    }
}
