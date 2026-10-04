package com.example.novav2.network

import org.junit.Assert.assertEquals
import org.junit.Test

/** An ask_choice question's options are read out by the app, not the model. */
class ChoiceWordingTest {
    private fun turn(confirmation: String?, options: List<String>?) = NovaApiClient.EventResult.Final(
        speech = "Which time works?", actions = emptyList(), editActions = emptyList(), deleteActions = emptyList(),
        timerActions = emptyList(), alarmActions = emptyList(), episodeId = null, confirmation = confirmation,
        scheduledDeparture = null, options = options,
    )

    @Test
    fun choice_endsWithItsOptions() {
        assertEquals(
            "Which time works? Thursday at 10, or Friday at 2?",
            turn("choice", listOf("Thursday at 10", "Friday at 2")).speechWithOptions,
        )
        assertEquals(
            "Which time works? Monday, Thursday at 10, or Friday at 2?",
            turn("choice", listOf("Monday", "Thursday at 10", "Friday at 2")).speechWithOptions,
        )
    }

    @Test
    fun anythingElse_isJustTheSpeech() {
        assertEquals("Which time works?", turn("yes_no", null).speechWithOptions)
        assertEquals("Which time works?", turn(null, listOf("stray")).speechWithOptions)
    }
}
