package com.example.novav2.network

import com.example.novav2.notes.CapturedNote
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The phone's side of the /notes wire format (server/app/api/notes.py) and the recall-chip parser. */
class NotesWireTest {
    @Test
    fun `captured note serialises to the POST body`() {
        val note = CapturedNote(
            id = "n1", createdAtMillis = 0, source = "device_voice", kind = "dictation", text = "hello",
            segments = listOf(CapturedNote.Segment(0.0, 1.5, "hello")),
            durationS = 1.5, calendarTitle = "COMP2100 Lecture", sttEngine = "vosk", sttAvgConf = 0.9,
        )
        val json = with(NotesApiClient) { note.toJson() }
        assertEquals("n1", json.getString("id"))
        assertEquals("1970-01-01T00:00:00Z", json.getString("created_at"))
        assertEquals("COMP2100 Lecture", json.getJSONObject("context").getString("calendar_title"))
        assertEquals(1.5, json.getJSONArray("segments").getJSONObject(0).getDouble("end_s"), 0.0)
        assertEquals("never", json.getString("summarise"))  // the phone asks for it separately
    }

    @Test
    fun `note parses, including offsets and nulls`() {
        val note = NotesApiClient.parseNote(JSONObject("""
            {"id":"n1","created_at":"2026-09-23T10:05:00+10:00","updated_at":"2026-09-23T00:05:00Z",
             "source":"device_voice","kind":"dictation","title":null,"text":"t","segments":[],
             "duration_s":null,"context":{"calendar_title":"COMP2100"},
             "stt":null,"tags":[],"summary":{"title":"T","tldr":"x","key_points":["a"],
             "action_items":[],"open_questions":[],"flagged_moments":[{"t_s":12,"quote":"q"}]},
             "summary_status":"done","promoted_fact_ids":["f1"]}
        """.trimIndent()))
        assertEquals("2026-09-23T00:05:00Z", note.createdAt.toString())
        assertNull(note.title)
        assertNull(note.durationS)
        assertEquals("T", note.displayTitle)
        assertEquals(12.0, note.summary!!.flaggedMoments.single().tS, 0.0)
        assertEquals(listOf("f1"), note.promotedFactIds)
    }

    @Test
    fun `recall actions - only memory recalls that ran`() {
        val actions = JSONArray("""
            [{"tool":"memory","input":{"action":"recall","query":"tutor","since":"2026-09-22T00:00:00"},"ran":true},
             {"tool":"memory","input":{"action":"save","text":"x"},"ran":true},
             {"tool":"memory","input":{"action":"recall","query":"no"},"ran":false},
             {"tool":"set_timer","input":{},"ran":true}]
        """.trimIndent())
        assertEquals(
            listOf(RecallAction(query = "tutor", since = "2026-09-22T00:00:00", until = null, kind = null)),
            parseRecallActions(actions),
        )
    }
}
