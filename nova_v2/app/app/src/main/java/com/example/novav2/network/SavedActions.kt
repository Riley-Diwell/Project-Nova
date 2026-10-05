package com.example.novav2.network

import org.json.JSONArray

/**
 * Something this turn kept for the user, and where it went - the Voice tab's "Saved to …" chip,
 * so a note, a memory and a reminder never look alike:
 *
 *   NOTE      kept as they said it          -> Notes tab (or the note itself, by [noteId])
 *   MEMORY    something true about them     -> Knowledge Map
 *   REMINDER  something to do later         -> Reminders tab
 *
 * Read off the memory and set_reminder Actions that ran. A memory save's `saved_as` is where the
 * tool actually put it (intent_surface._memory_outcome) - a durable save falls back to a note when
 * Persona is unreachable, so the model's `category` alone would sometimes say the wrong place.
 */
data class SavedAction(val kind: Kind, val text: String, val noteId: String? = null) {
    enum class Kind { NOTE, MEMORY, REMINDER }
}

internal fun parseSavedActions(actions: JSONArray?): List<SavedAction> {
    if (actions == null) return emptyList()
    val saves = actions.ranActions("memory").mapNotNull { action ->
        val input = action.optJSONObject("input") ?: return@mapNotNull null
        if (input.optString("action") != "save" || input.optBoolean("failed", false)) return@mapNotNull null
        val text = input.optString("text").trim().takeIf { it.isNotEmpty() } ?: return@mapNotNull null
        when (input.optString("saved_as")) {
            "memory" -> SavedAction(SavedAction.Kind.MEMORY, text)
            "note" -> SavedAction(
                SavedAction.Kind.NOTE, noteChipText(input.optString("title"), text),
                noteId = input.optString("note_id").takeIf { it.isNotBlank() },
            )
            // No outcome recorded - the save didn't land anywhere we can point to.
            else -> null
        }
    }
    val reminders = parseReminderActions(actions).map { SavedAction(SavedAction.Kind.REMINDER, it.text) }
    return saves + reminders
}

private val LINE_MARKER = Regex("""^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)""")
private val BOLD_MARKER = Regex("""\*\*|__""")

/**
 * What a note's chip says. A note Nova writes is laid out in Markdown ('## ' headings, '- '
 * bullets - server memory_tool.py), which the Notes tab renders but a one-line chip would show
 * raw ("Note: ## Door code"). So: its title if the model gave one, else its first line with the
 * markers dropped.
 */
internal fun noteChipText(title: String, text: String): String {
    title.trim().takeIf { it.isNotEmpty() }?.let { return it }
    val first = text.lines().map { it.replace(LINE_MARKER, "").replace(BOLD_MARKER, "").trim() }
        .firstOrNull { it.isNotEmpty() }
    return first ?: text.trim()
}
