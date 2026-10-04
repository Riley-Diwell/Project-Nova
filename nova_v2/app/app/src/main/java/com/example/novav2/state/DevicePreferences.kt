package com.example.novav2.state

import android.content.Context

/**
 * What a press of the device button does while nothing else is going on - one action per press
 * count, the user's choice (DeviceButtonPolicy decides when "nothing else" is true). Either a
 * built-in [IdleAction.Preset] or a [IdleAction.Custom] instruction, which is sent to Nova exactly
 * as if it had been spoken - so it gets the same tools, and the same confirmation, as a voice turn.
 */
sealed interface IdleAction {
    enum class Preset(val label: String, val prompt: String? = null) {
        NONE("Nothing"),
        /** One short buzz per reminder due in the next hour (up to three), one long if none. */
        STATUS("Buzz what's coming up"),
        REPEAT_LAST_REPLY("Repeat Nova's last reply"),
        WHATS_NEXT("What's next?", prompt = "What's next for me today?"),
        WHEN_TO_LEAVE("When do I leave?", prompt = "When do I need to leave for my next event?"),
    }

    data class Use(val preset: Preset) : IdleAction
    data class Custom(val instruction: String) : IdleAction

    companion object {
        /** "preset:WHATS_NEXT" / "custom:Text Sam I'm running late" - null for anything else, so
         * a value from a future version falls back to the default rather than misfiring. */
        fun decode(stored: String?): IdleAction? {
            if (stored == null) return null
            val kind = stored.substringBefore(':', missingDelimiterValue = "")
            val value = stored.substringAfter(':', missingDelimiterValue = "")
            return when (kind) {
                "preset" -> Preset.entries.firstOrNull { it.name == value }?.let(::Use)
                "custom" -> value.trim().takeIf { it.isNotEmpty() }?.let(::Custom)
                else -> null
            }
        }

        fun encode(action: IdleAction): String = when (action) {
            is Use -> "preset:${action.preset.name}"
            is Custom -> "custom:${action.instruction.trim()}"
        }
    }
}

/** The device's own settings. Same SharedPreferences file as [ReminderPreferences], so receivers
 * and services can read them with no UI running. */
object DevicePreferences {
    private const val PREFS_NAME = "nova_settings"
    private const val KEY_IDLE_ACTION = "device_idle_action_"

    /** Press counts the user can assign - more than three is too many to count reliably. */
    val IDLE_PRESS_COUNTS = 1..3

    /** One press is the easiest to make by accident, so it does the one harmless thing. */
    val DEFAULT_IDLE_ACTIONS: Map<Int, IdleAction> = mapOf(
        1 to IdleAction.Use(IdleAction.Preset.STATUS),
        2 to IdleAction.Use(IdleAction.Preset.WHATS_NEXT),
        3 to IdleAction.Use(IdleAction.Preset.REPEAT_LAST_REPLY),
    )

    fun idleActions(context: Context): Map<Int, IdleAction> {
        val prefs = prefs(context)
        return IDLE_PRESS_COUNTS.associateWith { count ->
            IdleAction.decode(prefs.getString(KEY_IDLE_ACTION + count, null))
                ?: DEFAULT_IDLE_ACTIONS.getValue(count)
        }
    }

    fun setIdleAction(context: Context, count: Int, action: IdleAction) {
        require(count in IDLE_PRESS_COUNTS) { "no idle action for $count presses" }
        prefs(context).edit().putString(KEY_IDLE_ACTION + count, IdleAction.encode(action)).apply()
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
