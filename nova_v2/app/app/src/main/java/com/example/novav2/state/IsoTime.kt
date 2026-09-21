package com.example.novav2.state

import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeParseException

/**
 * Parses an ISO-8601 timestamp into epoch millis. The backend's get_calendar_range tool is
 * asked to return UTC ISO 8601 (with a 'Z'), but it's LLM-produced input, not a validated wire
 * contract - a bare/offset-less string falls back to being read as the device's own local time
 * rather than crashing the round trip.
 *
 * Shared by VoiceScreen.kt and AssistVoiceService.kt, the two places that resolve a
 * get_calendar_range NeedMore hop and apply the calendar actions that come back after it.
 */
fun parseIsoToEpochMillis(iso: String): Long =
    try {
        Instant.parse(iso).toEpochMilli()
    } catch (e: DateTimeParseException) {
        LocalDateTime.parse(iso).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()
    }
