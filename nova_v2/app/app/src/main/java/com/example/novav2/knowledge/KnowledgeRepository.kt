package com.example.novav2.knowledge

import android.content.Context
import android.util.AtomicFile
import com.example.novav2.auth.AuthRepository
import com.example.novav2.auth.SessionState
import com.example.novav2.network.NovaApiClient
import com.example.novav2.network.NovaApiClient.KnowledgeGraph
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.io.FileNotFoundException
import java.io.IOException

/**
 * The Knowledge Map's graph, kept so opening the map shows what was there last time straight
 * away, and asks the server again in the background (stale-while-revalidate). Held in memory and
 * saved to a file with its ETag and owner, so a cold start - or no network - still has it.
 *
 * The user's own changes are applied here rather than by refetching the whole graph:
 *  - [base] is the last graph the server sent, with every change the server has confirmed
 *    applied on top.
 *  - [forgetting] and [relabelled] are changes still in flight. They are laid over [base] for
 *    display and over every graph a refresh brings back, so a refresh that raced a delete
 *    can't bring the fact back. A failed change is simply dropped from the overlay.
 *
 * Similarity links are recomputed server-side across every pair of facts, so a forget only
 * removes the fact's own links here; the next refresh brings any links that changed.
 *
 * Refreshes are conditional: [etag] (the server's tag for [base] as it arrived) goes back as
 * If-None-Match, and an unchanged Persona answers with a bodyless 304. Any change the user makes
 * here also changes the server's tag, so the refresh after one always brings the full graph.
 *
 * Main-thread only, like the screen that drives it. Cleared on sign-out
 * ([com.example.novav2.auth.LocalData.wipe]) and whenever a different user is signed in.
 */
object KnowledgeRepository {
    // How similar two facts must be before the map draws a discovered link between them. Fixed
    // rather than user-tunable: lower values drew so many links the map turned to noise.
    private const val LINK_DENSITY = 0.9f

    data class KnowledgeState(
        /** Null until the first graph arrives for this user. */
        val graph: KnowledgeGraph? = null,
        val refreshing: Boolean = false,
        /** Why the last refresh failed; the cached graph (if any) is still shown. */
        val error: String? = null,
    )

    private val _state = MutableStateFlow(KnowledgeState())
    val state: StateFlow<KnowledgeState> = _state.asStateFlow()

    private var owner: String? = null
    private var base: KnowledgeGraph? = null
    private var etag: String? = null
    private val forgetting = mutableSetOf<String>()
    private val relabelled = mutableMapOf<String, String>()
    private var again = false

    private var disk: GraphFile? = null
    // One write at a time, in the order they were asked for, so an older graph never lands last.
    private val diskQueue = Dispatchers.IO.limitedParallelism(1)

    /** From NovaApplication. Without it the graph just isn't saved between runs. */
    fun init(context: Context) {
        disk = GraphFile(File(context.applicationContext.filesDir, GRAPH_FILE))
    }

    /**
     * Asks the server for the whole graph. A call while one is in flight doesn't start another:
     * it has the running one go round once more when it finishes, since whatever prompted it
     * (a consolidation, say) may have changed the graph after that request was sent.
     */
    suspend fun refresh() {
        val user = currentUser() ?: return
        if (user != owner) {
            reset()
            owner = user
        }
        if (base == null) restore(user)
        if (_state.value.refreshing) {
            again = true
            return
        }
        _state.update { it.copy(refreshing = true, error = null) }
        try {
            do {
                again = false
                val fresh = NovaApiClient.getKnowledgeGraph(LINK_DENSITY, ifNoneMatch = etag.takeIf { base != null })
                if (owner != user) return // signed out or switched while it was in flight
                if (fresh != null) {
                    base = fresh.graph
                    etag = fresh.etag
                    publish()
                    save()
                }
            } while (again)
        } catch (e: IOException) {
            if (owner == user) _state.update { it.copy(error = e.message ?: "Couldn't load the knowledge map.") }
        } catch (e: JSONException) {
            if (owner == user) _state.update { it.copy(error = e.message ?: "Couldn't load the knowledge map.") }
        } finally {
            again = false
            _state.update { it.copy(refreshing = false) }
        }
    }

    /** Correct a belief. Shown at once; throws (and puts the old text back) if the server refuses. */
    suspend fun edit(factId: String, text: String) {
        relabelled[factId] = text
        publish()
        try {
            NovaApiClient.editFact(factId, text, null)
            base = base?.relabelled(mapOf(factId to text))
            save()
        } finally {
            relabelled.remove(factId)
            publish()
        }
    }

    /** Forget a belief. Gone at once; throws (and shows it again) if the server refuses. */
    suspend fun forget(factId: String) {
        forgetting += factId
        publish()
        try {
            NovaApiClient.deleteFact(factId)
            base = base?.without(setOf(factId))
            save()
        } finally {
            forgetting -= factId
            publish()
        }
    }

    /** Deleting a note deletes every belief promoted from it (server-side); mirror that here. */
    fun onNoteDeleted(noteId: String) {
        val graph = base ?: return
        val promoted = graph.nodes.filter { it.noteId == noteId }.mapTo(HashSet()) { it.id }
        if (promoted.isEmpty()) return
        base = graph.without(promoted)
        publish()
        save()
    }

    /**
     * Turns repeated behaviour into beliefs. New facts bring new links across the whole graph, so
     * this is the one change that does refetch it. Returns (derived, stated) counts.
     */
    suspend fun consolidate(): Pair<Int, Int> {
        val counts = NovaApiClient.consolidate()
        refresh()
        return counts
    }

    /** Signing out: forget the graph, saved copy included. */
    fun clear() {
        reset()
        disk?.let { d -> AuthRepository.appScope.launch(diskQueue) { d.delete() } }
    }

    /** Drops what is in memory only - a different user signing in, whose own saved copy may be on disk. */
    private fun reset() {
        owner = null
        base = null
        etag = null
        forgetting.clear()
        relabelled.clear()
        _state.value = KnowledgeState()
    }

    private fun publish() {
        val shown = base?.without(forgetting)?.relabelled(relabelled)
        _state.update { it.copy(graph = shown) }
    }

    /** The saved copy, if it belongs to [user]. Its ETag comes with it, so the first refresh can be a 304. */
    private suspend fun restore(user: String) {
        val d = disk ?: return
        val saved = withContext(Dispatchers.IO) { d.read() } ?: return
        if (saved.owner != user || owner != user || base != null) return
        base = saved.graph
        etag = saved.etag
        publish()
    }

    private fun save() {
        val d = disk ?: return
        val snapshot = SavedGraph(owner ?: return, etag, base ?: return)
        AuthRepository.appScope.launch(diskQueue) { d.write(snapshot) }
    }

    private fun currentUser(): String? =
        (AuthRepository.state.value as? SessionState.SignedIn)?.userId
}

private const val GRAPH_FILE = "knowledge_graph.json"

/** What is saved between runs: whose graph, the server's tag for it, and the graph itself. */
internal data class SavedGraph(val owner: String, val etag: String?, val graph: KnowledgeGraph) {
    fun encode(): String = JSONObject()
        .put("owner", owner)
        .put("etag", etag ?: JSONObject.NULL)
        .put("graph", with(NovaApiClient) { graph.toJson() })
        .toString()

    companion object {
        /** Null for anything unreadable - a missing cache is only a slower first open. */
        fun decode(text: String): SavedGraph? = try {
            val json = JSONObject(text)
            SavedGraph(
                owner = json.getString("owner"),
                etag = if (json.isNull("etag")) null else json.getString("etag"),
                graph = NovaApiClient.parseKnowledgeGraph(json.getJSONObject("graph")),
            )
        } catch (e: JSONException) {
            null
        }
    }
}

/** Written whole or not at all (AtomicFile), so a crash mid-write leaves the previous copy. */
private class GraphFile(path: File) {
    private val file = AtomicFile(path)

    fun read(): SavedGraph? = try {
        SavedGraph.decode(file.readFully().decodeToString())
    } catch (e: FileNotFoundException) {
        null
    } catch (e: IOException) {
        null
    }

    fun write(saved: SavedGraph) {
        val out = try {
            file.startWrite()
        } catch (e: IOException) {
            return
        }
        try {
            out.write(saved.encode().toByteArray())
            file.finishWrite(out)
        } catch (e: IOException) {
            file.failWrite(out)
        }
    }

    fun delete() = file.delete()
}
