package com.example.nova.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.nova.network.NovaApiClient
import kotlinx.coroutines.launch
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

private data class LaidOutNode(
    val node: NovaApiClient.GraphNode,
    var x: Float,
    var y: Float,
    var vx: Float = 0f,
    var vy: Float = 0f,
)

@Composable
fun KnowledgeMapScreen() {
    val scope = rememberCoroutineScope()

    var graph by remember { mutableStateOf<NovaApiClient.KnowledgeGraph?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var consolidating by remember { mutableStateOf(false) }
    var consolidateNote by remember { mutableStateOf<String?>(null) }
    var minSimilarity by remember { mutableFloatStateOf(0.65f) }
    var selected by remember { mutableStateOf<NovaApiClient.GraphNode?>(null) }

    var scale by remember { mutableFloatStateOf(1f) }
    var pan by remember { mutableStateOf(Offset.Zero) }

    fun reload() {
        loading = true
        error = null
        scope.launch {
            try {
                graph = NovaApiClient.getKnowledgeGraph(minSimilarity)
            } catch (e: Exception) {
                error = e.message ?: "Couldn't load the knowledge map."
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) { reload() }

    // Recomputed only when the graph itself changes - panning and zooming are
    // view transforms, not a reason to re-run the simulation.
    val layout = remember(graph) { graph?.let { layoutOf(it) } ?: emptyList() }

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Text("Knowledge Map", style = MaterialTheme.typography.headlineSmall)
        Text(
            "What NOVA believes about you. Dashed links are connections it found " +
                "on its own.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))

        // Consolidation is driven from here rather than from a timer, because
        // this is where the user can see the result: episodes they have lived
        // through turning into beliefs NOVA will act on. Doing it invisibly in
        // the background would work just as well and show nothing.
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(
                onClick = {
                    consolidating = true
                    error = null
                    scope.launch {
                        try {
                            val (derived, stated) = NovaApiClient.consolidate()
                            consolidateNote = when (derived + stated) {
                                0 -> "Nothing new to learn yet."
                                else -> "Learned $derived habit(s) and $stated thing(s) you said."
                            }
                            reload()
                        } catch (e: Exception) {
                            error = e.message ?: "Couldn't consolidate."
                        } finally {
                            consolidating = false
                        }
                    }
                },
                enabled = !consolidating && !loading,
            ) {
                Text(if (consolidating) "Learning…" else "Learn from my history")
            }
            consolidateNote?.let {
                Spacer(Modifier.width(8.dp))
                Text(
                    it,
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Spacer(Modifier.height(8.dp))

        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Link density", style = MaterialTheme.typography.labelMedium)
            Slider(
                value = minSimilarity,
                onValueChange = { minSimilarity = it },
                onValueChangeFinished = { reload() },
                valueRange = 0.45f..0.9f,
                modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
            )
            Text(String.format("%.2f", minSimilarity),
                style = MaterialTheme.typography.labelMedium)
        }

        Box(
            Modifier
                .fillMaxWidth()
                .weight(1f)
                .background(MaterialTheme.colorScheme.surfaceVariant,
                    RoundedCornerShape(12.dp)),
        ) {
            when {
                loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                error != null -> Text(
                    error!!,
                    Modifier.align(Alignment.Center).padding(24.dp),
                    color = MaterialTheme.colorScheme.error,
                )
                layout.isEmpty() -> Text(
                    "Nothing stored yet. Tell NOVA something about yourself, or " +
                        "run consolidation to build facts from your history.",
                    Modifier.align(Alignment.Center).padding(24.dp),
                    style = MaterialTheme.typography.bodyMedium,
                )
                else -> GraphCanvas(
                    layout = layout,
                    edges = graph?.edges.orEmpty(),
                    scale = scale,
                    pan = pan,
                    selectedId = selected?.id,
                    onTransform = { zoom, offset -> scale *= zoom; pan += offset },
                    onTapNode = { selected = it },
                )
            }
        }

        selected?.let { node ->
            Spacer(Modifier.height(8.dp))
            FactDetail(
                node = node,
                onDismiss = { selected = null },
                onDelete = {
                    scope.launch {
                        try {
                            NovaApiClient.deleteFact(node.id)
                            selected = null
                            reload()
                        } catch (e: Exception) {
                            error = e.message ?: "Couldn't delete that."
                        }
                    }
                },
                onSave = { newText ->
                    scope.launch {
                        try {
                            NovaApiClient.editFact(node.id, newText, null)
                            selected = null
                            reload()
                        } catch (e: Exception) {
                            error = e.message ?: "Couldn't save that."
                        }
                    }
                },
            )
        }
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
                    color = Color(0x33607D8B),
                    start = place(a), end = place(b), strokeWidth = 1.5f * scale,
                )
            }
        }

        layout.forEach { n ->
            val at = place(n)
            if (!n.node.isFact) {
                drawCircle(Color(0x66455A64), radius = 7f * scale, center = at)
                return@forEach
            }
            val fill = if (n.node.isDerived) Color(0xFF7E57C2) else Color(0xFF26A69A)
            val radius = (14f + 6f * (n.node.confidence ?: 1f)) * scale
            if (n.node.id == selectedId) {
                drawCircle(Color(0xFFFFC107), radius = radius + 6f * scale,
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
    val colour = Color(0xFF7E57C2).copy(alpha = alpha)
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

@Composable
private fun FactDetail(
    node: NovaApiClient.GraphNode,
    onDismiss: () -> Unit,
    onDelete: () -> Unit,
    onSave: (String) -> Unit,
) {
    var editing by remember(node.id) { mutableStateOf(false) }
    var draft by remember(node.id) { mutableStateOf(node.label) }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp)) {
            if (editing) {
                OutlinedTextField(
                    value = draft,
                    onValueChange = { draft = it },
                    label = { Text("What NOVA believes") },
                    modifier = Modifier.fillMaxWidth(),
                )
            } else {
                Text(node.label, style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold)
            }

            Spacer(Modifier.height(6.dp))
            Text(
                if (node.isDerived) {
                    "NOVA worked this out from your history" +
                        (node.support?.let { " - seen $it times" } ?: "")
                } else {
                    "You told NOVA this"
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            node.detail?.takeIf { it.isNotBlank() }?.let {
                Text("“$it”", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (node.category.isNotEmpty()) {
                Text(node.category.joinToString(" › "),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }

            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (editing) {
                    Button(onClick = { onSave(draft) }) { Text("Save") }
                    OutlinedButton(onClick = { editing = false; draft = node.label }) {
                        Text("Cancel")
                    }
                } else {
                    // Only beliefs can be edited or forgotten. A category node is
                    // the ontology skeleton - its id is a path ("cat:routines/places"),
                    // not a fact id, so offering Forget here sent Postgres something
                    // that is not a UUID and came back a 500. Categories exist as long
                    // as a fact is filed under them and vanish when the last one goes.
                    if (node.isFact) {
                        OutlinedButton(onClick = { editing = true }) { Text("Edit") }
                        OutlinedButton(onClick = onDelete) { Text("Forget") }
                        Spacer(Modifier.width(4.dp))
                    }
                    OutlinedButton(onClick = onDismiss) { Text("Close") }
                }
            }
        }
    }
}

/**
 * Force-directed layout: every node repels every other, edges pull like springs.
 * Run to a fixed step count rather than animated - the graph is small and does
 * not change until it is reloaded.
 */
private fun layoutOf(graph: NovaApiClient.KnowledgeGraph): List<LaidOutNode> {
    if (graph.nodes.isEmpty()) return emptyList()

    // Seeded so the same graph lays out the same way every time the tab is
    // opened; a map that rearranges itself on each visit is unreadable.
    val random = Random(graph.nodes.size * 31 + graph.edges.size)
    val nodes = graph.nodes.map {
        LaidOutNode(it, random.nextFloat() * 400f - 200f, random.nextFloat() * 400f - 200f)
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
