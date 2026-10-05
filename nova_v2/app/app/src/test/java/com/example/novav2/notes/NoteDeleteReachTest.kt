package com.example.novav2.notes

import com.example.novav2.data.NoteIdsColumn
import com.example.novav2.network.NovaApiClient
import com.example.novav2.network.RecallAction
import com.example.novav2.network.SavedAction
import com.example.novav2.network.parseRecallActions
import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Which Voice history bubbles a note's delete reaches (docs/plans/notes-hard-delete-plan.md S9):
 * every turn that saved, read back or quoted it, by id - and nothing else.
 */
class NoteDeleteReachTest {

    @Test
    fun theColumnMatchesWholeIdsOnly() {
        val column = NoteIdsColumn.encode(listOf("abc", "abcd", "abc"))!!
        assertEquals(",abc,abcd,", column)
        // What ChatMessageDao.deleteForNote's LIKE '%,' || id || ',%' does.
        fun matches(id: String) = column.contains(",$id,")
        assertTrue(matches("abc"))
        assertTrue(matches("abcd"))
        assertFalse(matches("ab"))
        assertFalse(matches("bcd"))
        assertEquals(listOf("abc", "abcd"), NoteIdsColumn.decode(column))
    }

    @Test
    fun noNotesMeansNoTag() {
        assertNull(NoteIdsColumn.encode(emptyList()))
        assertNull(NoteIdsColumn.encode(listOf("", " ")))
        assertEquals(emptyList<String>(), NoteIdsColumn.decode(null))
    }

    @Test
    fun aRecallCarriesTheNotesItReadOut() {
        val parsed = parseRecallActions(JSONArray(
            """[{"tool":"memory","ran":true,
                 "input":{"action":"recall","query":"gate code","note_ids":["n1","n2"]}}]"""
        ))
        assertEquals(listOf("n1", "n2"), parsed.single().noteIds)
    }

    @Test
    fun aRecallFromAnOlderServerHasNone() {
        val parsed = parseRecallActions(JSONArray(
            """[{"tool":"memory","ran":true,"input":{"action":"recall","query":"gate code"}}]"""
        ))
        assertEquals(emptyList<String>(), parsed.single().noteIds)
    }

    @Test
    fun aTurnReferencesWhatItSavedReadAndQuoted() {
        val turn = NovaApiClient.EventResult.Final(
            speech = "", actions = emptyList(), editActions = emptyList(), deleteActions = emptyList(),
            timerActions = emptyList(), alarmActions = emptyList(), episodeId = null, confirmation = null,
            scheduledDeparture = null,
            savedActions = listOf(
                SavedAction(SavedAction.Kind.NOTE, "the gate code is 4417", noteId = "saved"),
                SavedAction(SavedAction.Kind.MEMORY, "Allergic to peanuts"),
            ),
            recallActions = listOf(RecallAction(null, null, null, null, noteIds = listOf("read", "saved"))),
            noteIds = listOf("quoted"),
        )
        assertEquals(listOf("saved", "read", "quoted"), turn.referencedNoteIds)
    }
}
