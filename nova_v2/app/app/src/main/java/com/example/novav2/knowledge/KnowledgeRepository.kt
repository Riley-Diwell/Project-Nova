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
import org.json.JSONArray
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
 * The map's layout ([MapLayout]) is saved alongside the graph and only ever extended, never
 * redone, so the groups stay where the user last saw them - across refreshes, new facts and
 * cold starts alike. The camera (zoom and pan) is kept in memory for the life of the process.
 *
 * Learning from recent activity (consolidation) happens on its own now - see
 * [ConsolidationWorker]. This only reports it: [KnowledgeState.learning] while a run is going,
 * and the facts it added in [KnowledgeState.newlyLearned].
 *
 * Refreshes are conditional: [etag] (the server's tag for [base] as it arrived) goes back as
 * If-None-Match, and an unchanged Persona answers with a bodyless 304. Any change the user makes
 * here also changes the server's tag, so the refresh after one always brings the full graph.
 *
 * Main-thread only, like the screen that drives it - except [consolidationDue], which only
 * queues work. Cleared on sign-out ([com.example.novav2.auth.LocalData.wipe]) and whenever a
 * different user is signed in.
 */
object KnowledgeRepository {
    // How similar two facts must be before the map draws a discovered link between them. Fixed
    // rather than user-tunable: lower values drew so many links the map turned to noise.
    private const val LINK_DENSITY = 0.9f

    data class KnowledgeState(
        /** Null until the first graph arrives for this user. */
        val graph: KnowledgeGraph? = null,
        /** The subheadings, each with its facts. */
        val groups: List<MapGroup> = emptyList(),
        val layout: MapLayout = MapLayout(),
        val refreshing: Boolean = false,
        /** Why the last refresh failed; the cached graph (if any) is still shown. */
        val error: String? = null,
        /** A consolidation run is going (this phone's, or one the server reports). */
        val learning: Boolean = false,
        /** When NOVA last learned from recent activity, epoch millis, if known. */
        val lastLearnedAt: Long? = null,
        /** Facts the last run added, until the user has looked at them. */
        val newlyLearned: Set<String> = emptySet(),
    )

    private val _state = MutableStateFlow(KnowledgeState())
    val state: StateFlow<KnowledgeState> = _state.asStateFlow()

    /** Where the user left the map. Null until the screen first fits it to the view. */
    var camera: Camera? = null

    private var owner: String? = null
    private var base: KnowledgeGraph? = null
    private var etag: String? = null
    private var layout: MapLayout = MapLayout()
    private val forgetting = mutableSetOf<String>()
    private val relabelled = mutableMapOf<String, String>()
    private var again = false
    private var workerRunning = false

    private var disk: GraphFile? = null
    @Volatile private var appContext: Context? = null
    // One write at a time, in the order they were asked for, so an older graph never lands last.
    private val diskQueue = Dispatchers.IO.limitedParallelism(1)

    /** From NovaApplication. Without it the graph just isn't saved between runs. */
    fun init(context: Context) {
        appContext = context.applicationContext
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
                val fetch = NovaApiClient.getKnowledgeGraph(LINK_DENSITY, ifNoneMatch = etag.takeIf { base != null })
                if (owner != user) return // signed out or switched while it was in flight
                fetch.tagged?.let { fresh ->
                    base = fresh.graph
                    etag = fresh.etag
                    publish()
                    save()
                }
                fetch.hint?.let(::onHint)
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

    /**
     * Correct a belief. Shown at once; throws (and puts the old text back) if the server refuses.
     * A belief the edit contradicted is removed by the server - and here, straight away.
     */
    suspend fun edit(factId: String, text: String) {
        relabelled[factId] = text
        publish()
        try {
            val overruled = NovaApiClient.editFact(factId, text, null)
            base = base?.relabelled(mapOf(factId to text))?.without(overruled.toSet())
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
     * Search the map: by meaning on the server, or by words on the phone when the server can't
     * (it predates search, or it's unreachable). Empty for a query with nothing to match on.
     */
    suspend fun search(query: String): MapSearch {
        val current = _state.value
        val graph = current.graph ?: return MapSearch(query, emptyList(), emptyList(), local = true)
        val groupOf = current.groups.groupIndex()
        val fromServer = try {
            NovaApiClient.searchPersona(query)
        } catch (e: IOException) {
            null
        } catch (e: JSONException) {
            null
        }
        return if (fromServer != null) fromServer(query, fromServer, groupOf) else fromLocal(query, graph, groupOf)
    }

    // --- automatic learning (ConsolidationWorker) ------------------------------------------

    /** The server says it's time to learn from recent activity. Any thread. */
    fun consolidationDue() {
        appContext?.let { ConsolidationWorker.runSoon(it) }
    }

    /** From the worker, on the main thread. */
    fun onLearning(running: Boolean) {
        workerRunning = running
        _state.update { it.copy(learning = running) }
    }

    /** From the worker, on the main thread: fetch what the run wrote and mark what's new. */
    suspend fun onConsolidated(run: NovaApiClient.ConsolidationRun) {
        val before = factIds()
        refresh()
        val added = if (run.ran) factIds() - before else emptySet()
        _state.update {
            it.copy(
                lastLearnedAt = System.currentTimeMillis(),
                newlyLearned = if (added.isEmpty()) it.newlyLearned else it.newlyLearned + added,
            )
        }
    }

    /** The user has seen what was newly learned. */
    fun clearNewlyLearned() = _state.update { it.copy(newlyLearned = emptySet()) }

    private fun onHint(hint: NovaApiClient.ConsolidationHint) {
        if (hint.status == "due") consolidationDue()
        val lastRun = hint.lastRunAt?.let(::isoToMillis)
        _state.update {
            it.copy(
                learning = workerRunning || hint.status == "running",
                lastLearnedAt = listOfNotNull(it.lastLearnedAt, lastRun).maxOrNull(),
            )
        }
    }

    // --- housekeeping ------------------------------------------------------------------------

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
        layout = MapLayout()
        camera = null
        forgetting.clear()
        relabelled.clear()
        _state.value = KnowledgeState()
    }

    private fun factIds(): Set<String> =
        _state.value.graph?.nodes?.filter { it.isFact }?.mapTo(HashSet()) { it.id }.orEmpty()

    /**
     * Shows [base] with the in-flight changes over it, grouped and laid out. The layout is
     * extended from the last one rather than redone - see [layoutOf] - and is cheap enough to
     * run here on the main thread.
     */
    private fun publish() {
        val shown = base?.without(forgetting)?.relabelled(relabelled)
        val groups = shown?.let(::groupsOf).orEmpty()
        layout = layoutOf(groups, layout)
        val present = shown?.nodes?.mapTo(HashSet()) { it.id }.orEmpty()
        _state.update {
            it.copy(
                graph = shown,
                groups = groups,
                layout = layout,
                newlyLearned = it.newlyLearned intersect present,
            )
        }
    }

    /** The saved copy, if it belongs to [user]. Its ETag comes with it, so the first refresh can be a 304. */
    private suspend fun restore(user: String) {
        val d = disk ?: return
        val saved = withContext(Dispatchers.IO) { d.read() } ?: return
        if (saved.owner != user || owner != user || base != null) return
        base = saved.graph
        etag = saved.etag
        layout = saved.layout ?: MapLayout()
        publish()
    }

    private fun save() {
        val d = disk ?: return
        val snapshot = SavedGraph(owner ?: return, etag, base ?: return, layout)
        AuthRepository.appScope.launch(diskQueue) { d.write(snapshot) }
    }

    private fun currentUser(): String? =
        (AuthRepository.state.value as? SessionState.SignedIn)?.userId
}

private const val GRAPH_FILE = "knowledge_graph.json"

/**
 * What is saved between runs: whose graph, the server's tag for it, the graph itself, and where
 * the map put everything (so the groups are where the user left them after a restart).
 */
internal data class SavedGraph(
    val owner: String,
    val etag: String?,
    val graph: KnowledgeGraph,
    val layout: MapLayout? = null,
) {
    fun encode(): String = JSONObject()
        .put("owner", owner)
        .put("etag", etag ?: JSONObject.NULL)
        .put("graph", with(NovaApiClient) { graph.toJson() })
        .put("layout", layout?.toJson() ?: JSONObject.NULL)
        .toString()

    companion object {
        /** Null for anything unreadable - a missing cache is only a slower first open. */
        fun decode(text: String): SavedGraph? = try {
            val json = JSONObject(text)
            SavedGraph(
                owner = json.getString("owner"),
                etag = if (json.isNull("etag")) null else json.getString("etag"),
                graph = NovaApiClient.parseKnowledgeGraph(json.getJSONObject("graph")),
                // A copy saved before layouts were: laid out fresh, once.
                layout = json.optJSONObject("layout")?.let(::layoutFromJson),
            )
        } catch (e: JSONException) {
            null
        }
    }
}

internal fun MapLayout.toJson(): JSONObject = JSONObject()
    .put("groups", JSONArray(groups.map { (id, g) ->
        JSONObject().put("id", id).put("x", g.x.toDouble()).put("y", g.y.toDouble())
            .put("cols", g.cols).put("reserved", g.reservedRows)
    }))
    .put("slots", JSONArray(slots.map { (id, s) ->
        JSONObject().put("id", id).put("group", s.group).put("index", s.index)
    }))

/** Null if it doesn't read back cleanly: better a fresh layout than a half-remembered one. */
internal fun layoutFromJson(json: JSONObject): MapLayout? = try {
    val groups = json.getJSONArray("groups")
    val slots = json.getJSONArray("slots")
    MapLayout(
        groups = (0 until groups.length()).associate { i ->
            val g = groups.getJSONObject(i)
            g.getString("id") to GroupSlot(
                g.getDouble("x").toFloat(), g.getDouble("y").toFloat(), g.getInt("cols"), g.getInt("reserved"),
            )
        },
        slots = (0 until slots.length()).associate { i ->
            val s = slots.getJSONObject(i)
            s.getString("id") to FactSlot(s.getString("group"), s.getInt("index"))
        },
    )
} catch (e: JSONException) {
    null
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
