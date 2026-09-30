package com.example.novav2.notes.audio

import android.content.Context
import android.util.Log
import java.io.BufferedOutputStream
import java.io.DataOutputStream
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.TimeUnit

/**
 * Opt-in, phone-only audio for notes.
 *
 * Off by default. When on, a dictation keeps its raw ADPCM stream - exactly the
 * blocks the device sent, ~29 MB an hour, already compressed - in the app's private files, so
 * the user can replay from a transcript timestamp. It never leaves the phone: nothing here
 * uploads, and it lives in noBackupFilesDir, which Android's cloud backup never copies
 * (whatever the manifest's backup rules say). It is
 * deleted when its note is deleted, when the user turns the setting off, and after
 * [retentionDays] regardless.
 *
 * Commands to the assistant (plain holds) are never kept - only notes.
 */
object NoteAudioStore {
    private const val PREFS = "nova_notes"
    private const val KEY_ENABLED = "keep_audio"
    private const val KEY_DAYS = "keep_audio_days"
    private const val DEFAULT_DAYS = 7
    private const val DIR = "note_audio"
    private const val EXT = ".adpcm"
    private const val TAG = "NoteAudioStore"

    /** File header: magic, then the blocks as [u16 length][bytes]... */
    internal const val MAGIC = 0x4E564131 // "NVA1"

    fun isEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_ENABLED, false)

    fun setEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_ENABLED, enabled).apply()
        if (!enabled) deleteAll(context)
    }

    fun retentionDays(context: Context): Int = prefs(context).getInt(KEY_DAYS, DEFAULT_DAYS)

    fun setRetentionDays(context: Context, days: Int) =
        prefs(context).edit().putInt(KEY_DAYS, days.coerceIn(1, 90)).apply()

    fun fileFor(context: Context, noteId: String): File = File(dir(context), noteId + EXT)

    fun has(context: Context, noteId: String): Boolean = fileFor(context, noteId).exists()

    /** A writer for a new recording, or null if the user hasn't opted in. */
    fun newWriter(context: Context, noteId: String): NoteAudioWriter? =
        if (isEnabled(context)) NoteAudioWriter(fileFor(context, noteId)) else null

    fun delete(context: Context, noteId: String) {
        fileFor(context, noteId).delete()
    }

    fun deleteAll(context: Context) {
        dir(context).listFiles()?.forEach { it.delete() }
    }

    /** Deletes audio older than the retention window. */
    fun purgeExpired(context: Context) {
        val cutoff = System.currentTimeMillis() - TimeUnit.DAYS.toMillis(retentionDays(context).toLong())
        dir(context).listFiles()?.filter { it.lastModified() < cutoff }?.forEach {
            Log.i(TAG, "expiring kept audio ${it.name}")
            it.delete()
        }
    }

    private fun dir(context: Context) = File(context.noBackupFilesDir, DIR).apply { mkdirs() }

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}

/** Appends a recording's ADPCM blocks to its file as they arrive - nothing buffered in RAM. */
class NoteAudioWriter(private val file: File) {
    private val out = DataOutputStream(BufferedOutputStream(FileOutputStream(file))).apply {
        writeInt(NoteAudioStore.MAGIC)
    }

    fun write(adpcmBlock: ByteArray) {
        out.writeShort(adpcmBlock.size)
        out.write(adpcmBlock)
    }

    fun close() = out.close()

    /** The recording is being thrown away (nothing heard) - keep no audio for it. */
    fun discard() {
        runCatching { out.close() }
        file.delete()
    }
}
