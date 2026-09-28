package com.example.novav2.notes

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NoteRouterTest {
    @Test
    fun `plain prefixes`() {
        assertEquals("ask tutor about Q3", NoteRouter.match("note ask tutor about Q3"))
        assertEquals("buy milk", NoteRouter.match("note to self buy milk"))
        assertEquals("check the lab times", NoteRouter.match("take a note check the lab times"))
        assertEquals("room change to B204", NoteRouter.match("memo room change to B204"))
    }

    @Test
    fun `body keeps its original casing`() {
        assertEquals("COMP2100 quiz moved", NoteRouter.match("Note COMP2100 quiz moved"))
    }

    @Test
    fun `vocatives, fillers and joiners are stripped`() {
        assertEquals("parked on level 3", NoteRouter.match("nova note parked on level 3"))
        assertEquals("parked on level 3", NoteRouter.match("hey nova, note: parked on level 3"))
        assertEquals("the draft is due friday", NoteRouter.match("um note that the draft is due friday"))
    }

    @Test
    fun `false positives go to the assistant`() {
        assertNull(NoteRouter.match("not sure what time it is"))
        assertNull(NoteRouter.match("notice anything about my calendar"))
        assertNull(NoteRouter.match("notes from yesterday"))
        assertNull(NoteRouter.match("what did I note about the tutor"))
        assertNull(NoteRouter.match("remember I parked on level 3"))  // the assistant's path
        assertNull(NoteRouter.match("the note from yesterday"))
    }

    @Test
    fun `a prefix with nothing after it is not a note`() {
        assertNull(NoteRouter.match("note"))
        assertNull(NoteRouter.match("note that"))
        assertNull(NoteRouter.match("   "))
    }
}
