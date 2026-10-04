package com.example.novav2.ui.components

import org.junit.Assert.assertEquals
import org.junit.Test

/** How a note's text is laid out - Nova's Markdown-lite notes, and plain ones. */
class NoteTextTest {

    @Test
    fun aNoteNovaWroteBecomesHeadingsAndBullets() {
        val blocks = parseNoteBlocks(
            """
            ## Key ideas
            - **Zero-padding** sharpens the FFT
              - not resolution
            1. Max doppler: 500 Hz


            ## No quiz next week
            """.trimIndent()
        )
        assertEquals(
            listOf(
                NoteBlock.Heading(2, "Key ideas"),
                NoteBlock.Bullet(0, "**Zero-padding** sharpens the FFT"),
                NoteBlock.Bullet(1, "not resolution"),
                NoteBlock.Numbered("1", 0, "Max doppler: 500 Hz"),
                NoteBlock.Gap,                                  // two blank lines, one gap
                NoteBlock.Heading(2, "No quiz next week"),
            ),
            blocks,
        )
    }

    @Test
    fun aPoemKeepsItsLinesAndVerses() {
        val blocks = parseNoteBlocks("In the light of the moon,\na little egg lay on a leaf.\n\nOne Sunday morning")
        assertEquals(
            listOf(
                NoteBlock.Line("In the light of the moon,"),
                NoteBlock.Line("a little egg lay on a leaf."),
                NoteBlock.Gap,
                NoteBlock.Line("One Sunday morning"),
            ),
            blocks,
        )
    }

    @Test
    fun aHashWithoutASpaceIsNotAHeading() {
        assertEquals(listOf(NoteBlock.Line("#hashtag and C# notes")), parseNoteBlocks("#hashtag and C# notes"))
    }

    @Test
    fun boldMarkersAreDropped() {
        assertEquals("Zero-padding sharpens the FFT", inlineMarkup("**Zero-padding** sharpens __the FFT__").text)
    }
}
