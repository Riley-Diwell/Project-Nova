package com.example.novav2.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.novav2.auth.AuthRepository
import com.example.novav2.auth.SessionState
import com.example.novav2.network.NotesApiClient
import com.example.novav2.notes.CapturedNote
import com.example.novav2.notes.NoteSubmitResult
import com.example.novav2.notes.NotesRepository
import com.example.novav2.notes.audio.NoteAudioStore
import java.io.IOException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class NotesListState(
    val rows: List<NotesApiClient.NoteRow> = emptyList(),
    val query: String = "",
    val loading: Boolean = false,
    val error: String? = null,
    /** Notes captured offline, still waiting in the outbox. */
    val pending: Int = 0,
    /** Bumped each time a note is deleted, for the list's plain "Note deleted" notice. There
     * is no undo: a delete is confirmed first and then gone for good. */
    val deletedCount: Int = 0,
)

data class NoteDetailState(
    val note: NotesApiClient.Note? = null,
    val loading: Boolean = false,
    val busy: Boolean = false,
    val error: String? = null,
    val audioKept: Boolean = false,
)

/**
 * Backs the Notes tab and the note detail view. Activity-scoped (see NotesScreen) so a delete
 * started from the detail view can show its notice on the list it returns to.
 *
 * Without a search, the list is the phone's cache of it ([NotesRepository.rows]): it shows at
 * once - on a cold start, offline - and [refresh] replaces it from the server behind the scenes.
 * The user's own changes (edit, summarise, promote, add, delete) are written to the cache rather
 * than refetching the list. A search always asks the server; its results live only here.
 * Everything is dropped when the signed-in account changes.
 */
class NotesViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = NotesRepository(application)

    // Everything but the rows, which [list] fills in from the cache or the search.
    private val _list = MutableStateFlow(NotesListState())

    /** Null until the cache has been read once, so a cold start spins rather than saying "No notes yet". */
    private val cachedRows = repository.rows.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** The last search's results; only shown while [NotesListState.query] is not blank. */
    private val searchRows = MutableStateFlow<List<NotesApiClient.NoteRow>>(emptyList())

    /** Notes being deleted right now - hidden while the phone clears them out. */
    private val deleting = MutableStateFlow<Set<String>>(emptySet())

    val list: StateFlow<NotesListState> = combine(_list, cachedRows, searchRows, deleting) { s, cached, found, gone ->
        val searching = s.query.isNotBlank()
        s.copy(
            rows = (if (searching) found else cached.orEmpty()).filter { it.id !in gone },
            loading = s.loading || (!searching && cached == null),
        )
    }.stateIn(viewModelScope, SharingStarted.Eagerly, NotesListState(loading = true))

    private val _detail = MutableStateFlow(NoteDetailState())
    val detail: StateFlow<NoteDetailState> = _detail.asStateFlow()

    private var searchJob: Job? = null

    init {
        viewModelScope.launch {
            AuthRepository.state
                .map { (it as? SessionState.SignedIn)?.userId }
                .distinctUntilChanged()
                .drop(1)
                .collect {
                    // The cache itself is wiped with the account's other data (LocalData.wipe).
                    searchJob?.cancel()
                    _list.value = NotesListState()
                    searchRows.value = emptyList()
                    deleting.value = emptySet()
                    _detail.value = NoteDetailState()
                }
        }
    }

    fun refresh() {
        searchJob?.cancel()
        searchJob = viewModelScope.launch { load() }
    }

    fun setQuery(query: String) {
        _list.update { it.copy(query = query) }
        searchJob?.cancel()
        searchJob = viewModelScope.launch {
            delay(SEARCH_DEBOUNCE_MS)
            load()
        }
    }

    private suspend fun load() {
        val query = _list.value.query
        _list.update { it.copy(loading = true, error = null) }
        try {
            if (query.isBlank()) repository.syncList() else searchRows.value = NotesApiClient.list(query = query)
            _list.update { it.copy(loading = false, pending = repository.pendingCount()) }
        } catch (e: IOException) {
            _list.update { it.copy(loading = false, error = "Couldn't load notes: ${e.message}", pending = repository.pendingCount()) }
        }
    }

    /** "+" - a typed note. Goes through the same outbox as a voice note. */
    fun addTyped(text: String, onDone: (NoteSubmitResult) -> Unit = {}) {
        if (text.isBlank()) return
        viewModelScope.launch {
            val result = repository.submit(CapturedNote(
                source = CapturedNote.SOURCE_TYPED,
                kind = CapturedNote.KIND_QUICK,
                text = text.trim(),
                summarise = false,
            ))
            onDone(result)
            if (result is NoteSubmitResult.Saved) cacheSaved(result.noteId)
            // A search ranks server-side; ask rather than guess where the new note would land.
            if (_list.value.query.isNotBlank()) load()
            else _list.update { it.copy(pending = repository.pendingCount()) }
        }
    }

    /** Puts a just-saved note into the cached list: one note fetched instead of the list. */
    private suspend fun cacheSaved(noteId: String) {
        try {
            repository.cache(NotesApiClient.get(noteId).asRow())
        } catch (e: IOException) {
            // Saved all the same; the next refresh brings it.
        }
    }

    // --- delete ----------------------------------------------------------------------

    /**
     * Deletes the note for good - only ever called once the user has confirmed (NoteDetailScreen's
     * dialog). There is no undo window: deleted notes are entirely gone, and a window would mean
     * keeping them (docs/plans/notes-hard-delete-plan.md phase 3).
     */
    fun delete(noteId: String) {
        deleting.update { it + noteId }
        _list.update { it.copy(deletedCount = it.deletedCount + 1) }
        viewModelScope.launch {
            try {
                // Gone from this phone at once, and from the server now or once it's reachable
                // (NotesRepository.delete never fails for being offline).
                repository.delete(noteId)
                searchRows.update { rows -> rows.filter { it.id != noteId } }
            } finally {
                deleting.update { it - noteId }
            }
        }
    }

    // --- detail ---------------------------------------------------------------------

    fun openNote(noteId: String) {
        _detail.value = NoteDetailState(loading = true)
        viewModelScope.launch {
            try {
                val note = NotesApiClient.get(noteId)
                _detail.value = NoteDetailState(note = note, audioKept = NoteAudioStore.has(getApplication(), noteId))
            } catch (e: IOException) {
                _detail.value = NoteDetailState(error = "Couldn't open the note: ${e.message}")
            }
        }
    }

    fun saveEdits(title: String?, text: String?) = detailCall { id ->
        NotesApiClient.update(id, title = title, text = text)
    }

    fun resummarise() = detailCall { id -> NotesApiClient.summarise(id) }

    /** "Add to what Nova knows" - files the note in Persona under [category]. */
    fun promote(category: List<String>) = detailCall { id ->
        NotesApiClient.promote(id, category)
        NotesApiClient.get(id)
    }

    suspend fun markdown(noteId: String): String? = try {
        NotesApiClient.markdown(noteId)
    } catch (e: IOException) {
        _detail.update { it.copy(error = "Couldn't share: ${e.message}") }
        null
    }

    private fun detailCall(block: suspend (String) -> NotesApiClient.Note) {
        val id = _detail.value.note?.id ?: return
        _detail.update { it.copy(busy = true, error = null) }
        viewModelScope.launch {
            try {
                val note = block(id)
                _detail.update { it.copy(note = note, busy = false) }
                repository.cache(note.asRow())
                searchRows.update { rows ->
                    rows.map { r ->
                        if (r.id == note.id) note.asRow().copy(snippet = r.snippet, snippetStartS = r.snippetStartS) else r
                    }
                }
            } catch (e: IOException) {
                _detail.update { it.copy(busy = false, error = e.message) }
            }
        }
    }

    private fun NotesApiClient.Note.asRow() = NotesApiClient.NoteRow(
        id = id, createdAt = createdAt, source = source, kind = kind, title = displayTitle,
        preview = text.take(300), snippet = null, snippetStartS = null, tldr = summary?.tldr,
        durationS = durationS, calendarTitle = calendarTitle, summaryStatus = summaryStatus,
        promoted = promotedFactIds.isNotEmpty(),
    )

    companion object {
        private const val SEARCH_DEBOUNCE_MS = 300L
    }
}
