package com.example.novav2.network

import org.json.JSONArray

/**
 * A memory-tool recall the assistant ran this turn (tools/functions/memory_tool.py) - what the
 * Voice tab turns into chips that open the notes it answered from.
 *
 * The chips re-run the same search through GET /notes rather than the turn carrying note ids
 * back: the recall's result is the model's input, not part of the turn's wire contract, and the
 * search is deterministic enough that the same query and bounds find the same notes.
 * [since]/[until] are the LOCAL times the model resolved ("yesterday" -> a date), exactly as
 * GET /notes accepts them with utc_offset_minutes.
 *
 * [noteIds] are the notes the recall actually read out (intent_surface records them since
 * 2026-10-03, so that deleting a note deletes the turn that quoted it). Not used for the chips -
 * see above - but for tagging the turn's Voice history bubbles the same way. Empty from an older
 * server.
 */
data class RecallAction(
    val query: String?,
    val since: String?,
    val until: String?,
    val kind: String?,
    val noteIds: List<String> = emptyList(),
)

internal fun parseRecallActions(actions: JSONArray?): List<RecallAction> =
    actions?.ranActions("memory").orEmpty().mapNotNull { action ->
        val input = action.optJSONObject("input") ?: return@mapNotNull null
        if (input.optString("action") != "recall") return@mapNotNull null
        RecallAction(
            query = input.optString("query").trim().takeIf { it.isNotEmpty() },
            since = input.optString("since").trim().takeIf { it.isNotEmpty() },
            until = input.optString("until").trim().takeIf { it.isNotEmpty() },
            kind = input.optString("kind").takeIf { it in setOf("quick", "dictation") },
            noteIds = input.optJSONArray("note_ids")?.let { ids ->
                (0 until ids.length()).mapNotNull { ids.optString(it).takeIf(String::isNotBlank) }
            }.orEmpty(),
        )
    }
