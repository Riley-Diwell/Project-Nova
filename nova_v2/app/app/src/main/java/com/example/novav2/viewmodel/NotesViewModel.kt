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
    /** A delete the user can still undo (the server call waits [UNDO_WINDOW_MS]). */
    val pendingDelete: NotesApiClient.NoteRow? = null,
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
 * started from the detail view can show its Undo snackbar on the list it returns to.
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

    /** Notes whose delete has been committed but not yet confirmed by the server - kept hidden. */
    private val deleting = MutableStateFlow<Set<String>>(emptySet())

    val list: StateFlow<NotesListState> = combine(_list, cachedRows, searchRows, deleting) { s, cached, found, gone ->
        val searching = s.query.isNotBlank()
        val hidden = gone + listOfNotNull(s.pendingDelete?.id)
        s.copy(
            rows = (if (searching) found else cached.orEmpty()).filter { it.id !in hidden },
            loading = s.loading || (!searching && cached == null),
        )
    }.stateIn(viewModelScope, SharingStarted.Eagerly, NotesListState(loading = true))

    private val _detail = MutableStateFlow(NoteDetailState())
    val detail: StateFlow<NoteDetailState> = _detail.asStateFlow()

    private var searchJob: Job? = null
    private var deleteJob: Job? = null

    init {
        viewModelScope.launch {
            AuthRepository.state
                .map { (it as? SessionState.SignedIn)?.userId }
                .distinctUntilChanged()
                .drop(1)
                .collect {
                    // The cache itself is wiped with the account's other data (LocalData.wipe).
                    searchJob?.cancel()
                    deleteJob?.cancel()
                    deleteJob = null
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

    // --- delete with undo -----------------------------------------------------------

    /** Hides the note now and deletes it for real after [UNDO_WINDOW_MS] unless undone. */
    fun requestDelete(noteId: String) {
        deleteJob?.let { job ->
            // A second delete inside the window commits the first straight away.
            job.cancel()
            _list.value.pendingDelete?.let { commitDelete(it.id) }
        }
        val row = list.value.rows.firstOrNull { it.id == noteId }
            ?: _detail.value.note?.takeIf { it.id == noteId }?.let { it.asRow() }
            ?: return
        _list.update { it.copy(pendingDelete = row) }
        deleteJob = viewModelScope.launch {
            delay(UNDO_WINDOW_MS)
            commitDelete(noteId)
        }
    }

    /** The row was only ever hidden, so it reappears where it was. */
    fun undoDelete() {
        deleteJob?.cancel()
        deleteJob = null
        _list.update { it.copy(pendingDelete = null) }
    }

    private fun commitDelete(noteId: String) {
        deleting.update { it + noteId }
        _list.update { it.copy(pendingDelete = null) }
        deleteJob = null
        viewModelScope.launch {
            try {
                repository.delete(noteId) // drops it from the cache too
                searchRows.update { rows -> rows.filter { it.id != noteId } }
            } catch (e: IOException) {
                // Not deleted after all - letting it show again is the truth.
                _list.update { it.copy(error = "Couldn't delete: ${e.message}") }
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
        const val UNDO_WINDOW_MS = 5_000L
        private const val SEARCH_DEBOUNCE_MS = 300L
    }
}
