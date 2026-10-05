package com.example.novav2.network

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The Voice tab's "Saved to …" chips - a note, a memory and a reminder told apart. */
class SavedActionParsingTest {

    private fun actions(vararg json: String) = JSONArray("[${json.joinToString(",")}]")

    @Test
    fun eachKindLandsWhereTheServerSaidItDid() {
        val parsed = parseSavedActions(actions(
            """{"tool":"memory","trigger":"requested","ran":true,
               "input":{"action":"save","text":"submit the form by 5","saved_as":"note","note_id":"n1"}}""",
            """{"tool":"memory","trigger":"requested","ran":true,
               "input":{"action":"save","text":"Allergic to peanuts","category":["facts","health"],
                        "saved_as":"memory","outcome":"added"}}""",
            """{"tool":"set_reminder","trigger":"requested","ran":true,
               "input":{"text":"Submit the form","in_minutes":30}}""",
        ))
        assertEquals(
            listOf(
                SavedAction(SavedAction.Kind.NOTE, "submit the form by 5", noteId = "n1"),
                SavedAction(SavedAction.Kind.MEMORY, "Allergic to peanuts"),
                SavedAction(SavedAction.Kind.REMINDER, "Submit the form"),
            ),
            parsed,
        )
    }

    @Test
    fun aDurableSaveThatFellBackToANoteIsANote() {
        val parsed = parseSavedActions(actions(
            """{"tool":"memory","ran":true,"input":{"action":"save","text":"Likes bagels",
               "category":["opinions","likes","food"],"saved_as":"note","note_id":"n2"}}"""
        ))
        assertEquals(SavedAction.Kind.NOTE, parsed.single().kind)
    }

    @Test
    fun aMarkdownNoteChipShowsItsTitleOrPlainFirstLine() {
        val parsed = parseSavedActions(actions(
            """{"tool":"memory","ran":true,"input":{"action":"save","text":"## Door code\n- 4417",
               "title":"Door code 4417","saved_as":"note","note_id":"n3"}}""",
            """{"tool":"memory","ran":true,"input":{"action":"save","text":"## **Door code**\n- 4417",
               "saved_as":"note","note_id":"n4"}}""",
            """{"tool":"memory","ran":true,"input":{"action":"save","text":"\n- buy milk\n- eggs",
               "saved_as":"note","note_id":"n5"}}""",
        ))
        assertEquals(listOf("Door code 4417", "Door code", "buy milk"), parsed.map { it.text })
    }

    @Test
    fun failedRefusedAndRecallsMakeNoChip() {
        val parsed = parseSavedActions(actions(
            """{"tool":"memory","ran":true,"input":{"action":"save","text":"x","failed":true}}""",
            """{"tool":"memory","ran":false,"input":{"action":"save","text":"y","saved_as":"note"}}""",
            """{"tool":"memory","ran":true,"input":{"action":"recall","query":"parking"}}""",
            """{"tool":"set_reminder","ran":false,"input":{"text":"z","in_minutes":5}}""",
        ))
        assertTrue(parsed.isEmpty())
    }
}
