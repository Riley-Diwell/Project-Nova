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
 */
data class RecallAction(
    val query: String?,
    val since: String?,
    val until: String?,
    val kind: String?,
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
        )
    }
