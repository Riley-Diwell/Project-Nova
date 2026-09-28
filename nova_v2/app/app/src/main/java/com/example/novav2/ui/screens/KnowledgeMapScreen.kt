package com.example.novav2.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.example.novav2.knowledge.KnowledgeRepository
import com.example.novav2.network.NovaApiClient
import com.example.novav2.ui.components.ScreenGutter
import com.example.novav2.ui.components.ScreenHeader
import com.example.novav2.ui.theme.NovaAccentLight
import com.example.novav2.ui.theme.NovaBlue
import com.example.novav2.ui.theme.NovaDerived
import kotlinx.coroutines.launch
import org.json.JSONException
import java.io.IOException
import kotlin.math.hypot
import kotlin.math.sqrt
import kotlin.random.Random

/**
 * The Knowledge Map (DESIGN.md Section 5.6) - Persona rendered back to the user
 * as a navigable, editable graph. This is the visible face of the Agency and
 * Privacy pillars: what NOVA believes about you, where it got it, and the
 * ability to correct or delete any of it.
 *
 * TWO KINDS OF LINK, drawn differently on purpose:
 *  - solid, faint  = the ontology. Declared structure, always true.
 *  - dashed, bright = vector similarity. Discovered, and the interesting one -
 *    it connects facts nobody filed together. The two parking facts land under
 *    different category roots and still find each other this way.
 *
 * Layout is force-directed (repulsion between all nodes, springs along edges),
 * simulated for a fixed number of steps when the graph loads rather than
 * continuously - the graph is small and static, so a settled layout costs a few
 * milliseconds once and avoids animating a canvas forever behind the user's back.
 */
private const val LAYOUT_STEPS = 320
private const val REPULSION = 14000f
private const val SPRING = 0.014f
private const val DAMPING = 0.85f

// Category nodes pull their children in tighter than similarity links do, so
// the declared hierarchy reads as the skeleton and discovered links as bridges.
private const val CATEGORY_REST_LENGTH = 90f
private const val SIMILAR_REST_LENGTH = 170f

private val STATED_COLOUR = NovaBlue
private val SIMILAR_COLOUR = NovaAccentLight

private data class LaidOutNode(
    val node: NovaApiClient.GraphNode,
    var x: Float,
    var y: Float,
    var vx: Float = 0f,
    var vy: Float = 0f,
)

@Composable
fun KnowledgeMapScreen(onOpenNote: (String) -> Unit = {}) {
    val scope = rememberCoroutineScope()

    // Held by the repository across visits: the last map shows at once and is revalidated behind it.
    val knowledge by KnowledgeRepository.state.collectAsState()
    val graph = knowledge.graph
    // An edit, forget or consolidate that failed. A failed refresh is knowledge.error.
    var actionError by remember { mutableStateOf<String?>(null) }
    val error = actionError ?: knowledge.error
    val loading = graph == null && knowledge.refreshing
    var consolidating by remember { mutableStateOf(false) }
    var consolidateNote by remember { mutableStateOf<String?>(null) }
    // By id, so an edited belief shows its new text and a forgotten one closes its card.
    var selectedId by remember { mutableStateOf<String?>(null) }
    val selected = graph?.nodes?.firstOrNull { it.id == selectedId }
    var editing by remember { mutableStateOf<NovaApiClient.GraphNode?>(null) }
    var forgetting by remember { mutableStateOf<NovaApiClient.GraphNode?>(null) }

    var scale by remember { mutableFloatStateOf(1f) }
    var pan by remember { mutableStateOf(Offset.Zero) }

    fun reload() {
        actionError = null
        scope.launch { KnowledgeRepository.refresh() }
    }

    LaunchedEffect(Unit) { reload() }

    // Recomputed only when the graph itself changes - panning and zooming are
    // view transforms, not a reason to re-run the simulation. Each run starts
    // from the last one's positions, so forgetting a fact doesn't reshuffle the map.
    val lastLayout = remember { arrayOfNulls<List<LaidOutNode>>(1) }
    val layout = remember(graph) {
        (graph?.let { layoutOf(it, lastLayout[0]) } ?: emptyList()).also { lastLayout[0] = it }
    }

    Column(Modifier.fillMaxSize()) {
        ScreenHeader(
            title = "Knowledge Map",
            subtitle = "What NOVA believes about you. Tap a dot to see where it came from.",
        )

        Column(
            Modifier.fillMaxWidth().weight(1f).padding(start = ScreenGutter, end = ScreenGutter, bottom = 16.dp),
        ) {
            // Consolidation is driven from here rather than from a timer, because
            // this is where the user can see the result: episodes they have lived
            // through turning into beliefs NOVA will act on. Doing it invisibly in
            // the background would work just as well and show nothing.
            FilledTonalButton(
                onClick = {
                    consolidating = true
                    actionError = null
                    scope.launch {
                        try {
                            val (derived, stated) = KnowledgeRepository.consolidate()
                            consolidateNote = when (derived + stated) {
                                0 -> "Nothing new to learn yet."
                                else -> "Learned $derived habit(s) and $stated thing(s) you said."
                            }
                        } catch (e: IOException) {
                            actionError = e.message ?: "Couldn't consolidate."
                        } catch (e: JSONException) {
                            actionError = e.message ?: "Couldn't consolidate."
                        } finally {
                            consolidating = false
                        }
                    }
                },
                enabled = !consolidating && !loading,
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (consolidating) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(10.dp))
                    Text("Learning…")
                } else {
                    Icon(Icons.Default.AutoAwesome, contentDescription = null, Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Learn from my history")
                }
            }
            consolidateNote?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
            if (error != null && layout.isNotEmpty()) {
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

            // The selected belief sits up here, under the button, so the map below keeps its
            // full height rather than being pushed down by a card at the bottom.
            selected?.let { node ->
                Spacer(Modifier.height(12.dp))
                FactDetail(
                    node = node,
                    onDismiss = { selectedId = null },
                    onOpenNote = onOpenNote,
                    onEdit = { editing = node },
                    onForget = { forgetting = node },
                )
            }

            Spacer(Modifier.height(12.dp))
            Legend()
            Spacer(Modifier.height(8.dp))

            Box(
                Modifier
                    .fillMaxWidth()
                    .weight(1f)
                    .background(MaterialTheme.colorScheme.surfaceContainerLow, MaterialTheme.shapes.medium)
                    .border(1.dp, MaterialTheme.colorScheme.outlineVariant, MaterialTheme.shapes.medium),
            ) {
                when {
                    loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                    // With a map to show, an error sits above it instead of replacing it.
                    error != null && layout.isEmpty() -> Column(
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
                    layout.isEmpty() -> Text(
                        "Nothing stored yet. Tell NOVA something about yourself, or " +
                            "tap Learn from my history to build facts from what you've done.",
                        Modifier.align(Alignment.Center).padding(24.dp),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                    )
                    else -> GraphCanvas(
                        layout = layout,
                        edges = graph?.edges.orEmpty(),
                        scale = scale,
                        pan = pan,
                        selectedId = selected?.id,
                        onTransform = { zoom, offset -> scale *= zoom; pan += offset },
                        onTapNode = { selectedId = it?.id },
                    )
                }
            }
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
        AlertDialog(
            onDismissRequest = { forgetting = null },
            title = { Text("Forget this?") },
            text = { Text("NOVA will stop believing “${node.label}” and won't use it again.") },
            confirmButton = {
                TextButton(onClick = {
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
                }) { Text("Forget", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { forgetting = null }) { Text("Cancel") } },
        )
    }
}

/** What the dots and lines mean, so the map reads without the explainer text. */
@Composable
private fun Legend() {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        LegendItem("You told NOVA") { drawCircle(STATED_COLOUR) }
        LegendItem("NOVA worked out") { drawCircle(NovaDerived) }
        LegendItem("Found a link") {
            drawLine(SIMILAR_COLOUR, Offset(0f, center.y), Offset(size.width * 0.4f, center.y), 2.dp.toPx())
            drawLine(SIMILAR_COLOUR, Offset(size.width * 0.6f, center.y), Offset(size.width, center.y), 2.dp.toPx())
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

@Composable
private fun GraphCanvas(
    layout: List<LaidOutNode>,
    edges: List<NovaApiClient.GraphEdge>,
    scale: Float,
    pan: Offset,
    selectedId: String?,
    onTransform: (Float, Offset) -> Unit,
    onTapNode: (NovaApiClient.GraphNode?) -> Unit,
) {
    val positions = remember(layout) { layout.associateBy { it.node.id } }
    val selectionRing = MaterialTheme.colorScheme.onBackground
    val categoryColour = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.45f)
    val ontologyColour = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.18f)

    Canvas(
        Modifier
            .fillMaxSize()
            // Panning/zooming (and a spread-out layout) can otherwise push nodes
            // past the box's edge, where they'd draw over whatever sits next to
            // it in the Column instead of staying inside the defined map area.
            .clipToBounds()
            .pointerInput(layout) {
                detectTransformGestures { _, panChange, zoomChange, _ ->
                    onTransform(zoomChange, panChange)
                }
            }
            .pointerInput(layout, scale, pan) {
                detectTapGestures { tap ->
                    val centre = Offset(size.width / 2f, size.height / 2f)
                    // Undo the same transform the draw pass applies, so a tap
                    // lands on what the user actually sees.
                    val world = (tap - centre - pan) / scale
                    val hit = layout
                        .filter { it.node.isFact }
                        .minByOrNull { hypot(it.x - world.x, it.y - world.y) }
                    onTapNode(
                        hit?.takeIf { hypot(it.x - world.x, it.y - world.y) < 44f }
                            ?.node
                    )
                }
            },
    ) {
        val centre = Offset(size.width / 2f, size.height / 2f)
        fun place(n: LaidOutNode) = centre + pan + Offset(n.x, n.y) * scale

        edges.forEach { edge ->
            val a = positions[edge.source] ?: return@forEach
            val b = positions[edge.target] ?: return@forEach
            if (edge.isSimilarity) {
                drawSimilarityLink(place(a), place(b), edge.weight)
            } else {
                drawLine(
                    color = ontologyColour,
                    start = place(a), end = place(b), strokeWidth = 1.5f * scale,
                )
            }
        }

        layout.forEach { n ->
            val at = place(n)
            if (!n.node.isFact) {
                drawCircle(categoryColour, radius = 7f * scale, center = at)
                return@forEach
            }
            val fill = if (n.node.isDerived) NovaDerived else STATED_COLOUR
            val radius = (14f + 6f * (n.node.confidence ?: 1f)) * scale
            if (n.node.id == selectedId) {
                drawCircle(selectionRing, radius = radius + 6f * scale,
                    center = at, style = Stroke(width = 3f * scale))
            }
            drawCircle(fill, radius = radius, center = at)
        }
    }
}

/** Dashed, and brighter the closer the two facts are - a discovered connection
 *  should look different from a declared one at a glance. */
private fun DrawScope.drawSimilarityLink(a: Offset, b: Offset, weight: Float) {
    val alpha = (0.25f + (weight - 0.5f)).coerceIn(0.2f, 0.9f)
    val colour = SIMILAR_COLOUR.copy(alpha = alpha)
    val delta = b - a
    val length = hypot(delta.x, delta.y)
    if (length <= 0f) return

    val step = delta / length
    var travelled = 0f
    while (travelled < length) {
        val segment = minOf(9f, length - travelled)
        drawLine(
            color = colour,
            start = a + step * travelled,
            end = a + step * (travelled + segment),
            strokeWidth = 2f,
        )
        travelled += 16f
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun FactDetail(
    node: NovaApiClient.GraphNode,
    onDismiss: () -> Unit,
    onEdit: () -> Unit,
    onForget: () -> Unit,
    onOpenNote: (String) -> Unit = {},
) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh),
    ) {
        Column(Modifier.padding(start = 16.dp, top = 4.dp, end = 4.dp, bottom = 12.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Column(Modifier.weight(1f).padding(top = 12.dp, end = 4.dp)) {
                    Text(node.label, style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        if (node.isDerived) {
                            "NOVA worked this out from your history" +
                                (node.support?.let { " - seen $it times" } ?: "")
                        } else {
                            "You told NOVA this"
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = if (node.isDerived) NovaDerived else MaterialTheme.colorScheme.tertiary,
                    )
                }
                IconButton(onClick = onDismiss) {
                    Icon(Icons.Default.Close, contentDescription = "Close")
                }
            }
            Column(Modifier.padding(end = 12.dp)) {
                node.detail?.takeIf { it.isNotBlank() }?.let {
                    Text("“$it”", style = MaterialTheme.typography.bodySmall,
                        fontStyle = FontStyle.Italic,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 6.dp))
                }
                if (node.category.isNotEmpty()) {
                    Text(node.category.joinToString(" › ") { it.replaceFirstChar(Char::uppercase) },
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 4.dp))
                }

                // Only beliefs can be edited or forgotten. A category node is
                // the ontology skeleton - its id is a path ("cat:routines/places"),
                // not a fact id, so offering Forget here sent Postgres something
                // that is not a UUID and came back a 500. Categories exist as long
                // as a fact is filed under them and vanish when the last one goes.
                if (node.isFact) {
                    Spacer(Modifier.height(10.dp))
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        OutlinedButton(onClick = onEdit) {
                            Icon(Icons.Default.Edit, contentDescription = null, Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Edit")
                        }
                        OutlinedButton(
                            onClick = onForget,
                            colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                        ) {
                            Icon(Icons.Default.Delete, contentDescription = null, Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Forget")
                        }
                        // A belief promoted from a note links back to it. Forget removes the
                        // belief and keeps the note; deleting the note removes both.
                        node.noteId?.let { noteId ->
                            TextButton(onClick = { onOpenNote(noteId) }) { Text("Open note") }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun EditFactDialog(
    node: NovaApiClient.GraphNode,
    onDismiss: () -> Unit,
    onSave: (String) -> Unit,
) {
    var draft by remember(node.id) { mutableStateOf(node.label) }
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { focus.requestFocus() }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Edit what NOVA believes") },
        text = {
            OutlinedTextField(
                value = draft,
                onValueChange = { draft = it },
                label = { Text("Belief") },
                minLines = 2,
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
                modifier = Modifier.fillMaxWidth().focusRequester(focus),
            )
        },
        confirmButton = {
            TextButton(
                onClick = { onSave(draft.trim()) },
                enabled = draft.isNotBlank() && draft.trim() != node.label,
            ) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/**
 * Force-directed layout: every node repels every other, edges pull like springs.
 * Run to a fixed step count rather than animated - the graph is small and does
 * not change until it is reloaded. Nodes already in [previous] start where they
 * settled last time, so an edit or a forget nudges the map rather than redrawing it.
 */
private fun layoutOf(graph: NovaApiClient.KnowledgeGraph, previous: List<LaidOutNode>? = null): List<LaidOutNode> {
    if (graph.nodes.isEmpty()) return emptyList()

    // Seeded so the same graph lays out the same way every time the tab is
    // opened; a map that rearranges itself on each visit is unreadable.
    val random = Random(graph.nodes.size * 31 + graph.edges.size)
    val settled = previous?.associateBy { it.node.id }.orEmpty()
    val nodes = graph.nodes.map {
        val x = random.nextFloat() * 400f - 200f
        val y = random.nextFloat() * 400f - 200f
        settled[it.id]?.let { was -> LaidOutNode(it, was.x, was.y) } ?: LaidOutNode(it, x, y)
    }
    val byId = nodes.associateBy { it.node.id }

    repeat(LAYOUT_STEPS) {
        for (i in nodes.indices) {
            val a = nodes[i]
            for (j in i + 1 until nodes.size) {
                val b = nodes[j]
                var dx = a.x - b.x
                var dy = a.y - b.y
                var distanceSquared = dx * dx + dy * dy
                if (distanceSquared < 0.01f) {
                    dx = random.nextFloat() - 0.5f
                    dy = random.nextFloat() - 0.5f
                    distanceSquared = 0.01f
                }
                val force = REPULSION / distanceSquared
                val distance = sqrt(distanceSquared)
                a.vx += dx / distance * force; a.vy += dy / distance * force
                b.vx -= dx / distance * force; b.vy -= dy / distance * force
            }
        }

        graph.edges.forEach { edge ->
            val a = byId[edge.source] ?: return@forEach
            val b = byId[edge.target] ?: return@forEach
            val rest = if (edge.isSimilarity) SIMILAR_REST_LENGTH else CATEGORY_REST_LENGTH
            val dx = b.x - a.x
            val dy = b.y - a.y
            val distance = hypot(dx, dy).coerceAtLeast(0.01f)
            val pull = (distance - rest) * SPRING
            a.vx += dx / distance * pull; a.vy += dy / distance * pull
            b.vx -= dx / distance * pull; b.vy -= dy / distance * pull
        }

        nodes.forEach { n ->
            n.vx *= DAMPING; n.vy *= DAMPING
            n.x += n.vx.coerceIn(-30f, 30f)
            n.y += n.vy.coerceIn(-30f, 30f)
        }
    }
    return nodes
}
