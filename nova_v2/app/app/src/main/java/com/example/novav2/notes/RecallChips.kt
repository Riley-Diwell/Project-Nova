package com.example.novav2.notes

import com.example.novav2.network.NotesApiClient
import com.example.novav2.network.RecallAction
import java.io.IOException
import java.time.ZoneId
import java.time.ZonedDateTime

/** The notes behind a memory-tool recall, for the Voice tab's chips (see [RecallAction]). */
object RecallChips {
    private const val MAX_CHIPS = 3

    /** Re-runs the recall's search; empty on any failure - chips are a shortcut, never an error. */
    suspend fun find(recall: RecallAction): List<NotesApiClient.NoteRow> = try {
        NotesApiClient.list(
            query = recall.query,
            kind = recall.kind,
            since = recall.since,
            until = recall.until,
            utcOffsetMinutes = ZonedDateTime.now(ZoneId.systemDefault()).offset.totalSeconds / 60,
            limit = MAX_CHIPS,
        )
    } catch (e: IOException) {
        emptyList()
    }
}
