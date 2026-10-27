package com.example.novav2.state

import android.util.Log
import com.example.novav2.network.NovaApiClient
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * "Sure, checking your calendar now." - said while a turn runs, so a slow reply isn't heard as
 * silence (server: app/control/ack.py). Shared by ChatViewModel and AssistVoiceService.
 *
 * The line is asked for alongside /event and handed to `say` only if the turn is still running
 * [DELAY_MS] in, so a turn that answers quickly never hears it. Whatever happens to the turn, the
 * request for the line is cancelled with it: a reply never waits on an acknowledgement.
 *
 * Logged under [TAG] (`adb logcat -s NovaAck`): on 2026-10-28 the server saw no /event/ack at all
 * for two turns in four, and this says which step went missing. Never the words themselves.
 */
object TurnAck {
    const val DELAY_MS = 800L
    const val TAG = "NovaAck"

    suspend fun <T> around(transcript: String, say: (String) -> Unit, turn: suspend () -> T): T =
        coroutineScope {
            val started = System.currentTimeMillis()
            fun since() = System.currentTimeMillis() - started
            Log.d(TAG, "asking for a line (${transcript.length} chars)")
            val ack = async {
                NovaApiClient.getAck(transcript).also {
                    Log.d(TAG, "line ${if (it == null) "none" else "back"} after ${since()} ms")
                }
            }
            val speaker = launch {
                delay(DELAY_MS)
                ack.await()?.let {
                    Log.d(TAG, "saying the line at ${since()} ms")
                    say(it)
                }
            }
            try {
                turn()
            } finally {
                Log.d(TAG, "turn done after ${since()} ms" +
                    if (ack.isActive) " - line still pending, cancelled" else "")
                speaker.cancel()
                ack.cancel()
            }
        }
}
