package com.example.novav2.ui.screens.knowledgemap

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animate
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.ZoomOutMap
import androidx.compose.material3.AssistChip
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import com.example.novav2.knowledge.Box as WorldBox
import com.example.novav2.knowledge.Camera
import com.example.novav2.knowledge.CameraMath
import com.example.novav2.knowledge.KnowledgeRepository
import com.example.novav2.knowledge.Lod
import com.example.novav2.knowledge.MapSearch
import com.example.novav2.knowledge.agoText
import com.example.novav2.knowledge.groupIndex
import com.example.novav2.knowledge.groupsFor
import com.example.novav2.network.NovaApiClient
import com.example.novav2.ui.components.ScreenGutter
import com.example.novav2.ui.components.ScreenHeader
import com.example.novav2.ui.theme.NovaAccentLight
import com.example.novav2.ui.theme.NovaBlue
import com.example.novav2.ui.theme.NovaDerived
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONException
import java.io.IOException

/** How long after the last keystroke the map searches. */
private const val SEARCH_DEBOUNCE_MS = 350L
private const val SEARCH_MIN_CHARS = 2
private const val FLY_MS = 450
/** A fly-to never ends further out than this, so the matching facts are visible, not just their group. */
private const val SEARCH_MIN_ZOOM = 0.6f

/**
 * The Knowledge Map (DESIGN.md Section 5.6) - Persona rendered back to the user, grouped under
 * subheadings, searchable, and editable. This is the visible face of the Agency and Privacy
 * pillars: what NOVA believes about you, where it got it, and the ability to correct or delete
 * any of it.
 *
 * Zoomed out it reads as a page of topics; zooming in turns each topic into its facts, then each
 * fact into a card that says what it is and where it came from - no tap needed to skim. The
 * search bar at the bottom flies to the topic a query lands in and lights up what matched.
 *
 * Learning from recent activity happens on its own (knowledge/ConsolidationWorker); the line
 * under the header only reports it. Where everything sits is remembered (knowledge/
 * KnowledgeMapLayout), so new facts appear in place without moving what the user already knows.
 */
@Composable
fun KnowledgeMapScreen(onOpenNote: (String) -> Unit = {}) {
    val scope = rememberCoroutineScope()
    val focusManager = LocalFocusManager.current
    val density = LocalDensity.current.density

    // Held by the repository across visits: the last map shows at once and is revalidated behind it.
    val knowledge by KnowledgeRepository.state.collectAsState()
    val graph = knowledge.graph
    val groups = knowledge.groups
    val layout = knowledge.layout
    // An edit or forget that failed. A failed refresh is knowledge.error.
    var actionError by remember { mutableStateOf<String?>(null) }
    val error = actionError ?: knowledge.error
    val loading = graph == null && knowledge.refreshing
    val hasFacts = groups.isNotEmpty()

    // By id, so an edited belief shows its new text and a forgotten one closes its card.
    var selectedId by remember { mutableStateOf<String?>(null) }
    val selected = graph?.nodes?.firstOrNull { it.id == selectedId && it.isFact }
    var editing by remember { mutableStateOf<NovaApiClient.GraphNode?>(null) }
    var forgetting by remember { mutableStateOf<NovaApiClient.GraphNode?>(null) }

    // The view. Kept by the repository, so leaving the tab and coming back doesn't reset it.
    var camera by remember { mutableStateOf(KnowledgeRepository.camera) }
    var viewport by remember { mutableStateOf(IntSize.Zero) }
    val lastLod = remember { arrayOfNulls<Lod>(1) }
    val lod = camera?.let { CameraMath.lodFor(it.zoom, lastLod[0]) }.also { lastLod[0] = it }
    var flight by remember { mutableStateOf<Job?>(null) }

    // Search: the query survives rotation; the result is recomputed from it.
    var query by rememberSaveable { mutableStateOf("") }
    var result by remember { mutableStateOf<MapSearch?>(null) }
    var caption by remember { mutableStateOf<String?>(null) }
    var resultGroup by remember { mutableIntStateOf(0) }

    fun setCamera(c: Camera) {
        camera = c
        KnowledgeRepository.camera = c
    }

    fun flyTo(target: Camera) {
        flight?.cancel()
        val from = camera ?: return setCamera(target)
        flight = scope.launch {
            animate(0f, 1f, animationSpec = tween(FLY_MS, easing = FastOutSlowInEasing)) { t, _ ->
                setCamera(CameraMath.lerp(from, target, t))
            }
        }
    }

    fun fitCamera(box: WorldBox, minZoom: Float? = null): Camera? {
        if (viewport == IntSize.Zero) return null
        val padding = 24f * density
        val fitted = CameraMath.fit(box, viewport.width.toFloat(), viewport.height.toFloat(), density, padding, maxZoom = 1.2f)
        return if (minZoom != null && fitted.zoom < minZoom) CameraMath.withZoom(fitted, minZoom) else fitted
    }

    fun flyToGroup(groupId: String, minZoom: Float? = null) {
        layout.groupBox(groupId)?.let { box -> fitCamera(box, minZoom)?.let(::flyTo) }
    }

    fun flyToFacts(ids: Collection<String>) {
        val box = ids.mapNotNull { id ->
            layout.factPosition(id)?.let { WorldBox(it.x - 90f, it.y - 50f, it.x + 90f, it.y + 50f) }
        }.reduceOrNull(WorldBox::union) ?: return
        fitCamera(box, SEARCH_MIN_ZOOM)?.let(::flyTo)
    }

    fun showAll() {
        layout.bounds()?.let { box -> fitCamera(box)?.let(::flyTo) }
    }

    fun reload() {
        actionError = null
        scope.launch { KnowledgeRepository.refresh() }
    }

    fun showResult(found: MapSearch?, label: (MapSearch) -> String) {
        result = found
        resultGroup = 0
        caption = found?.let { if (it.hits.isEmpty()) "Nothing close to that" else label(it) }
        found?.target?.let { flyToGroup(it, SEARCH_MIN_ZOOM) }
    }

    fun groupTitle(id: String?) = groups.firstOrNull { it.id == id }?.title

    fun searchCaption(found: MapSearch): String {
        val n = found.hits.size
        val where = groupTitle(found.groups.getOrNull(resultGroup))?.let { " · in $it" } ?: ""
        return "$n match${if (n == 1) "" else "es"}$where"
    }

    suspend fun runSearch(text: String) {
        val trimmed = text.trim()
        if (trimmed.length < SEARCH_MIN_CHARS) {
            showResult(null) { "" }
            return
        }
        showResult(KnowledgeRepository.search(trimmed), ::searchCaption)
    }

    LaunchedEffect(Unit) { reload() }

    // The first time there's both a map and a view to put it in, fit the whole map.
    LaunchedEffect(viewport, layout) {
        if (camera == null && viewport != IntSize.Zero) {
            layout.bounds()?.let { box -> fitCamera(box)?.let(::setCamera) }
        }
    }

    LaunchedEffect(query) {
        delay(SEARCH_DEBOUNCE_MS)
        runSearch(query)
    }

    Column(Modifier.fillMaxSize().imePadding()) {
        ScreenHeader(
            title = "Knowledge Map",
            subtitle = "What NOVA believes about you, grouped by topic. Pinch in to read each one.",
        )

        Column(
            Modifier.fillMaxWidth().weight(1f).padding(start = ScreenGutter, end = ScreenGutter, bottom = 12.dp),
        ) {
            LearningStatus(
                learning = knowledge.learning,
                lastLearnedAt = knowledge.lastLearnedAt,
                newCount = knowledge.newlyLearned.size,
                onShowNew = {
                    val fresh = knowledge.newlyLearned
                    val index = groups.groupIndex()
                    val hits = fresh.map { MapSearch.Hit(it, 1f, keyword = false) }
                    result = MapSearch("", hits, groupsFor(hits, index), local = true)
                    resultGroup = 0
                    caption = "${fresh.size} new thing${if (fresh.size == 1) "" else "s"} learned"
                    flyToFacts(fresh)
                    KnowledgeRepository.clearNewlyLearned()
                },
            )

            if (error != null && hasFacts) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        error,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(onClick = { reload() }) { Text("Try again") }
                }
            }

            // The selected belief sits up here, so the map below keeps its full height.
            selected?.let { node ->
                Spacer(Modifier.height(8.dp))
                FactDetail(
                    node = node,
                    groupTitle = groupTitle(groups.groupIndex()[node.id]),
                    onDismiss = { selectedId = null },
                    onOpenNote = onOpenNote,
                    onEdit = { editing = node },
                    onForget = { forgetting = node },
                )
            }

            Spacer(Modifier.height(8.dp))
            Legend()
            Spacer(Modifier.height(8.dp))

            Box(
                Modifier
                    .fillMaxWidth()
                    .weight(1f)
                    .background(MaterialTheme.colorScheme.surfaceContainerLow, MaterialTheme.shapes.medium)
                    .border(1.dp, MaterialTheme.colorScheme.outlineVariant, MaterialTheme.shapes.medium),
            ) {
                val cam = camera
                when {
                    loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                    // With a map to show, an error sits above it instead of replacing it.
                    error != null && !hasFacts -> Column(
                        Modifier.align(Alignment.Center).padding(24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text(
                            error,
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodyMedium,
                            textAlign = TextAlign.Center,
                        )
                        Spacer(Modifier.height(8.dp))
                        TextButton(onClick = { reload() }) { Text("Try again") }
                    }
                    !hasFacts || graph == null -> Text(
                        "Nothing stored yet. Tell NOVA something about yourself - it also learns " +
                            "from what you do, on its own, and it will show up here.",
                        Modifier.align(Alignment.Center).padding(24.dp),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                    )
                    else -> {
                        KnowledgeMapCanvas(
                            graph = graph,
                            groups = groups,
                            layout = layout,
                            camera = cam ?: Camera(),
                            lod = lod ?: Lod.GROUPS,
                            selectedId = selected?.id,
                            highlight = result,
                            newlyLearned = knowledge.newlyLearned,
                            onCamera = ::setCamera,
                            onGesture = { flight?.cancel() },
                            onViewport = { viewport = it },
                            onTapFact = { selectedId = it },
                            onTapGroup = { flyToGroup(it, CameraMath.DOTS_AT + 0.15f) },
                            modifier = Modifier.fillMaxSize(),
                        )
                        IconButton(onClick = ::showAll, modifier = Modifier.align(Alignment.TopEnd)) {
                            Icon(Icons.Default.ZoomOutMap, contentDescription = "Show the whole map",
                                tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }

            SearchCaption(
                caption = caption,
                groupCount = result?.groups?.size ?: 0,
                onStep = { step ->
                    val found = result ?: return@SearchCaption
                    resultGroup = (resultGroup + step).mod(found.groups.size)
                    if (found.query.isNotEmpty()) caption = searchCaption(found)
                    flyToGroup(found.groups[resultGroup], SEARCH_MIN_ZOOM)
                },
            )

            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                placeholder = { Text("Search what NOVA knows…") },
                leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                trailingIcon = if (query.isNotEmpty() || result != null) {
                    {
                        IconButton(onClick = {
                            query = ""
                            showResult(null) { "" }
                            focusManager.clearFocus()
                        }) { Icon(Icons.Default.Close, "Clear search") }
                    }
                } else null,
                shape = RoundedCornerShape(28.dp),
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = {
                    focusManager.clearFocus()
                    scope.launch { runSearch(query) }
                }),
            )
        }
    }

    editing?.let { node ->
        EditFactDialog(
            node = node,
            onDismiss = { editing = null },
            onSave = { newText ->
                editing = null
                actionError = null
                scope.launch {
                    try {
                        KnowledgeRepository.edit(node.id, newText)
                    } catch (e: IOException) {
                        actionError = e.message ?: "Couldn't save that."
                    } catch (e: JSONException) {
                        actionError = e.message ?: "Couldn't save that."
                    }
                }
            },
        )
    }

    forgetting?.let { node ->
        ForgetFactDialog(
            node = node,
            onDismiss = { forgetting = null },
            onForget = {
                forgetting = null
                actionError = null
                scope.launch {
                    try {
                        KnowledgeRepository.forget(node.id)
                    } catch (e: IOException) {
                        actionError = e.message ?: "Couldn't delete that."
                    } catch (e: JSONException) {
                        actionError = e.message ?: "Couldn't delete that."
                    }
                }
            },
        )
    }
}

/**
 * Where the "Learn from my history" button used to be: learning happens on its own now, so this
 * only says what it's doing - and offers to show what it just learned.
 */
@Composable
private fun LearningStatus(learning: Boolean, lastLearnedAt: Long?, newCount: Int, onShowNew: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().height(36.dp)) {
        if (learning) {
            CircularProgressIndicator(Modifier.size(12.dp), strokeWidth = 1.5.dp)
            Spacer(Modifier.width(8.dp))
        }
        Text(
            when {
                learning -> "Learning from recent activity…"
                lastLearnedAt != null ->
                    "Learns as you use NOVA · updated ${agoText(lastLearnedAt, System.currentTimeMillis())}"
                else -> "Learns as you use NOVA"
            },
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        if (newCount > 0) {
            AssistChip(
                onClick = onShowNew,
                label = { Text("$newCount new") },
                leadingIcon = { Icon(Icons.Default.AutoAwesome, contentDescription = null, Modifier.size(16.dp)) },
            )
        }
    }
}

/** "4 matches · in Food", with arrows to step through the other groups that matched. */
@Composable
private fun SearchCaption(caption: String?, groupCount: Int, onStep: (Int) -> Unit) {
    if (caption == null) return
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().padding(top = 4.dp)) {
        Text(
            caption,
            style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f).padding(start = 4.dp),
        )
        if (groupCount > 1) {
            IconButton(onClick = { onStep(-1) }) {
                Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, contentDescription = "Previous group")
            }
            IconButton(onClick = { onStep(1) }) {
                Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = "Next group")
            }
        }
    }
}

/** What the colours and lines mean, so the map reads without the explainer text. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Legend() {
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        LegendItem("You told NOVA") { drawCircle(NovaBlue) }
        LegendItem("NOVA worked out") { drawCircle(NovaDerived) }
        LegendItem("Related") {
            drawLine(NovaAccentLight, Offset(0f, center.y), Offset(size.width * 0.4f, center.y), 2.dp.toPx())
            drawLine(NovaAccentLight, Offset(size.width * 0.6f, center.y), Offset(size.width, center.y), 2.dp.toPx())
        }
    }
}

@Composable
private fun LegendItem(label: String, swatch: DrawScope.() -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Canvas(Modifier.size(10.dp), onDraw = swatch)
        Spacer(Modifier.width(6.dp))
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
