package com.example.novav2.ble

/** Records what would have gone to the device, in order: a [NovaLedLayer], haptic steps
 * (List<Int>), "clear <id>", "mode <mode>/<token>/<timeoutSeconds>", "heading <degrees|none>". */
class FakeCommandSender : NovaCommandSender {
    val sent = mutableListOf<Any>()
    override fun sendHapticPulse(durationMs: Int) {}
    override fun sendLedPulse(durationMs: Int) {}
    override fun sendHapticPattern(stepsMs: List<Int>) { sent.add(stepsMs) }
    override fun sendSetLayer(layer: NovaLedLayer) { sent += layer }
    override fun sendClearLayer(id: Int) { sent += "clear $id" }
    override fun sendSetMode(mode: Int, token: Int, timeoutSeconds: Int) { sent += "mode $mode/$token/$timeoutSeconds" }
    override fun sendSetHeading(degrees: Double?) { sent += "heading ${degrees ?: "none"}" }
}
