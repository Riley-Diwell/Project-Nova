package com.example.novav2.notes

/**
 * Decides whether a held-button utterance is a quick note ("note ask tutor about Q3") rather
 * than a request for the assistant.
 *
 * Deterministic on purpose, like the server's commands.py ("grammar is arithmetic"): a prefix
 * match on the phone, no model round trip, so the note is saved word for word in about a
 * second and nothing is spoken. "Remember …" is deliberately NOT a prefix here - it goes to the
 * assistant, which decides whether the thing is durable enough to become part of what Nova
 * knows. "Note …" is verbatim and never promoted automatically.
 */
object NoteRouter {
    /** Longest first, so "note to self" wins over "note". */
    private val PREFIXES = listOf("note to self", "take a note", "make a note", "note", "memo")

    /** Leading words the user may address the device with, stripped before matching - mirrors
     * the vocative handling in server/app/control/commands.py. */
    private val VOCATIVES = setOf("nova", "hey", "hi", "ok", "okay", "please")

    /** Filler Vosk often emits before speech starts. */
    private val FILLERS = setOf("um", "uh", "er", "erm")

    /** Words that may sit between the prefix and the note, e.g. "note that …", "note: …". */
    private val JOINERS = setOf("that", "to", "down")

    /**
     * The note body, verbatim (original casing), if [transcript] starts with a note prefix;
     * otherwise null and the utterance goes to the assistant.
     */
    fun match(transcript: String): String? {
        val words = transcript.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
        if (words.isEmpty()) return null

        var i = 0
        while (i < words.size && normalise(words[i]).let { it in VOCATIVES || it in FILLERS }) i++

        for (prefix in PREFIXES) {
            val prefixWords = prefix.split(' ')
            if (i + prefixWords.size > words.size) continue
            val matches = prefixWords.indices.all { k -> normalise(words[i + k]) == prefixWords[k] }
            if (!matches) continue

            var j = i + prefixWords.size
            // "note that the draft is due" -> "the draft is due"; but only when something
            // follows, so "note that" alone is not a note of nothing.
            if (j < words.size - 1 && normalise(words[j]) in JOINERS) j++
            val rest = words.drop(j)
            if (rest.all { normalise(it) in JOINERS || normalise(it).isEmpty() }) return null
            val body = rest.joinToString(" ").trim().trimStart(':', ',', '-', ' ')
            return body.takeIf { it.isNotBlank() }
        }
        return null
    }

    /** Lowercase, punctuation stripped from the ends - "Note," and "note:" match "note". */
    private fun normalise(word: String): String =
        word.lowercase().trim { !it.isLetterOrDigit() }
}
