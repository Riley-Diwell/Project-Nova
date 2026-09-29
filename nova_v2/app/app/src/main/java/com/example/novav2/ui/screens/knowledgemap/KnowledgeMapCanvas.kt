package com.example.novav2.ui.screens.knowledgemap

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.IntSize
import com.example.novav2.knowledge.Box
import com.example.novav2.knowledge.Camera
import com.example.novav2.knowledge.CameraMath
import com.example.novav2.knowledge.Lod
import com.example.novav2.knowledge.MapGroup
import com.example.novav2.knowledge.MapLayout
import com.example.novav2.knowledge.MapSearch
import com.example.novav2.knowledge.Vec
import com.example.novav2.network.NovaApiClient.GraphNode
import com.example.novav2.network.NovaApiClient.KnowledgeGraph
import com.example.novav2.ui.theme.NovaAccentLight
import com.example.novav2.ui.theme.NovaBlue
import com.example.novav2.ui.theme.NovaDerived
import kotlin.math.max

/** Anything outside what's selected or searched for fades back to this. */
private const val DIMMED = 0.22f

/** Fact labels fade in across this zoom range, so they arrive gradually rather than all at once. */
private const val LABELS_FROM = 0.45f
private const val LABELS_FULL = 0.75f

/** Narrower than this, a label says too little to be worth drawing. */
private const val MIN_LABEL_DP = 56f

/** How close (in dp on screen) a tap has to be to a node to pick it. */
private const val TAP_SLOP_DP = 22f

/** Only pairs at least this similar are drawn as lines. Weaker ones (down to the server's 0.55)
 *  still shape the layout - they set how close facts sit - but drawn, they'd be noise. */
private const val DRAWN_LINK_MIN = 0.65f

/**
 * The map: topic hubs with their facts around them, drawn like Obsidian's graph view. Grey lines
 * join a fact to its own topic and to any other topic it is also about; blue lines join related
 * facts. How close things sit says how related they are (see [layoutOf]). Labels wrap onto as many
 * lines as they need (a few; more close up), sit beside their dot facing away from its topic, and
 * never overlap: where two would collide, the less important one is left out until the user zooms
 * in far enough for both. Pinch zooms about the fingers, drag pans, tap picks a fact or flies into
 * a topic. Only what's on screen is drawn or measured.
 */
@Composable
internal fun KnowledgeMapCanvas(
    graph: KnowledgeGraph,
    groups: List<MapGroup>,
    layout: MapLayout,
    camera: Camera,
    lod: Lod,
    selectedId: String?,
    highlight: MapSearch?,
    newlyLearned: Set<String>,
    onCamera: (Camera) -> Unit,
    onGesture: () -> Unit,
    onViewport: (IntSize) -> Unit,
    onTapFact: (String?) -> Unit,
    onTapGroup: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val density = LocalDensity.current.density
    val measurer = rememberTextMeasurer(cacheSize = 512)
    val nodes = remember(graph) { graph.nodes.associateBy { it.id } }
    // What a selected fact lights up: the facts it's drawn linked to, and the other topics it's
    // also about.
    val neighbours = remember(graph) {
        val out = HashMap<String, MutableSet<String>>()
        graph.edges.forEach { e ->
            when {
                e.isSimilarity && e.weight >= DRAWN_LINK_MIN -> {
                    out.getOrPut(e.source) { HashSet() } += e.target
                    out.getOrPut(e.target) { HashSet() } += e.source
                }
                e.isTopicLink -> out.getOrPut(e.source) { HashSet() } += e.target
            }
        }
        out
    }
    val hubOf = remember(groups) { groups.flatMap { g -> g.factIds.map { it to g.id } }.toMap() }

    // Gestures outlive recompositions; read the latest of these rather than the first.
    val currentCamera by rememberUpdatedState(camera)
    val currentLayout by rememberUpdatedState(layout)
    val currentGroups by rememberUpdatedState(groups)

    val colours = MapColours(
        hub = MaterialTheme.colorScheme.onSurfaceVariant,
        spoke = MaterialTheme.colorScheme.outlineVariant,
        hubLabel = MaterialTheme.colorScheme.onSurface,
        factLabel = MaterialTheme.colorScheme.onSurfaceVariant,
        selection = MaterialTheme.colorScheme.onBackground,
        hit = MaterialTheme.colorScheme.primary,
        fresh = MaterialTheme.colorScheme.tertiary,
    )
    val hubStyle = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.SemiBold)
    val factStyle = MaterialTheme.typography.labelSmall

    Canvas(
        modifier
            .clipToBounds()
            .onSizeChanged(onViewport)
            .pointerInput(Unit) {
                detectTransformGestures { centroid, pan, zoom, _ ->
                    onGesture()
                    val focus = Vec(centroid.x - size.width / 2f, centroid.y - size.height / 2f)
                    val zoomed = CameraMath.zoomAbout(currentCamera, focus, zoom, density)
                    onCamera(CameraMath.panBy(zoomed, pan.x, pan.y))
                }
            }
            .pointerInput(Unit) {
                detectTapGestures { tap ->
                    val cam = currentCamera
                    val k = cam.zoom * density
                    fun screen(w: Vec) = Offset(size.width / 2f + cam.panX + w.x * k, size.height / 2f + cam.panY + w.y * k)
                    val slop = TAP_SLOP_DP * density
                    var best: Pair<String, Boolean>? = null   // (id, isHub)
                    var bestDistance = Float.MAX_VALUE
                    for (group in currentGroups) {
                        currentLayout.hubPosition(group.id)?.let { hub ->
                            val d = (screen(hub) - tap).getDistance()
                            val reach = max(MapLayout.hubRadius(group.factIds.size) * k, slop)
                            if (d <= reach && d < bestDistance) { best = group.id to true; bestDistance = d }
                        }
                        for (id in group.factIds) {
                            val p = currentLayout.factPosition(id) ?: continue
                            val d = (screen(p) - tap).getDistance()
                            if (d <= slop && d < bestDistance) { best = id to false; bestDistance = d }
                        }
                    }
                    val picked = best
                    when {
                        picked == null -> onTapFact(null)
                        picked.second -> onTapGroup(picked.first)
                        else -> onTapFact(picked.first)
                    }
                }
            },
    ) {
        val k = camera.zoom * density
        fun at(w: Vec) = Offset(size.width / 2f + camera.panX + w.x * k, size.height / 2f + camera.panY + w.y * k)
        val topLeft = CameraMath.toWorld(camera, Vec(-size.width / 2f, -size.height / 2f), density)
        val bottomRight = CameraMath.toWorld(camera, Vec(size.width / 2f, size.height / 2f), density)
        val view = Box(topLeft.x, topLeft.y, bottomRight.x, bottomRight.y)

        // What stays bright: the selected fact with its topic and related facts, or the search
        // hits with their topics. Null means nothing is singled out, so everything is.
        val focus: Set<String>? = when {
            selectedId != null -> buildSet {
                add(selectedId); hubOf[selectedId]?.let(::add); neighbours[selectedId]?.let(::addAll)
            }
            highlight != null -> buildSet {
                addAll(highlight.factIds); highlight.factIds.forEach { id -> hubOf[id]?.let(::add) }
            }
            else -> null
        }
        fun alphaOf(id: String) = if (focus == null || id in focus) 1f else DIMMED
        val keywordOnly = highlight?.hits?.filter { it.keyword }?.mapTo(HashSet()) { it.factId }.orEmpty()

        val visible = groups.filter { g -> layout.groupBox(g.id)?.intersects(view) == true }
        val hubPx = { g: MapGroup -> max(MapLayout.hubRadius(g.factIds.size) * k, 7f * density) }
        val factPx = { node: GraphNode? ->
            max(MapLayout.FACT_RADIUS * k * (0.8f + 0.4f * (node?.confidence ?: 1f)), 3f * density)
        }

        // 1. Spokes: each fact to its topic.
        for (group in visible) {
            val hub = layout.hubPosition(group.id) ?: continue
            for (id in group.factIds) {
                val p = layout.factPosition(id) ?: continue
                val a = minOf(alphaOf(id), alphaOf(group.id)) * 0.7f
                drawLine(colours.spoke, at(hub), at(p), strokeWidth = (1.2f * density).coerceAtMost(1.2f * k), alpha = a)
            }
        }

        // 1b. A fact's links to the other topics it's also about - grey like its spoke, fainter.
        for (edge in graph.edges) {
            if (!edge.isTopicLink) continue
            val p = layout.factPosition(edge.source) ?: continue
            val hub = layout.hubPosition(edge.target) ?: continue
            if (!view.contains(p) && !view.contains(hub)) continue
            val a = minOf(alphaOf(edge.source), alphaOf(edge.target)) * 0.45f
            drawLine(colours.spoke, at(hub), at(p), strokeWidth = (1.2f * density).coerceAtMost(1.2f * k), alpha = a)
        }

        // 2. Links between related facts - from mid zoom, or always for what's singled out.
        for (edge in graph.edges) {
            if (!edge.isSimilarity || edge.weight < DRAWN_LINK_MIN) continue
            val bothFocused = focus != null && edge.source in focus && edge.target in focus
            if (lod == Lod.OVERVIEW && !bothFocused) continue
            val a = layout.factPosition(edge.source) ?: continue
            val b = layout.factPosition(edge.target) ?: continue
            if (!view.contains(a) && !view.contains(b)) continue
            val strength = (0.25f + (edge.weight - 0.65f) * 2f).coerceIn(0.2f, 0.7f)
            val dim = if (focus == null || bothFocused) 1f else DIMMED
            drawLine(NovaAccentLight, at(a), at(b), strokeWidth = 1.2f * density, alpha = strength * dim)
        }

        // 3. Fact dots, with rings for what's selected, found or new.
        val labels = ArrayList<LabelJob>()
        val obstacles = ArrayList<Rect>()
        val centre = Offset(size.width / 2f, size.height / 2f)
        for (group in visible) {
            for (id in group.factIds) {
                val p = layout.factPosition(id) ?: continue
                if (!view.inflate(40f).contains(p)) continue
                val node = nodes[id]
                val c = at(p)
                val r = factPx(node)
                val alpha = alphaOf(id)
                val ring = when {
                    id == selectedId -> colours.selection
                    highlight?.factIds?.contains(id) == true -> colours.hit
                    id in newlyLearned -> colours.fresh
                    else -> null
                }
                if (ring != null) {
                    val dashed = id in keywordOnly && id != selectedId
                    drawCircle(ring, r + 4f * density, c, style = Stroke(
                        width = 2f * density,
                        pathEffect = if (dashed) PathEffect.dashPathEffect(floatArrayOf(6f * density, 4f * density)) else null,
                    ))
                }
                drawCircle(node?.let(::factColour) ?: NovaBlue, r, c, alpha = alpha)

                obstacles += Rect(c.x - r, c.y - r, c.x + r, c.y + r)
                val singled = ring != null
                val fade = if (singled) 1f else ((camera.zoom - LABELS_FROM) / (LABELS_FULL - LABELS_FROM)).coerceIn(0f, 1f)
                val hub = layout.hubPosition(group.id)
                if (node != null && fade > 0.02f && hub != null) {
                    labels += LabelJob(
                        text = node.label, node = c, gap = r + 4f * density,
                        // Away from the hub, so the label never sits on its own spoke.
                        side = sideFacing(c - at(hub)),
                        style = factStyle, colour = colours.factLabel, alpha = alpha * fade,
                        // Wrapped, not cut short: a memory reads in full once there's room.
                        maxLines = if (lod == Lod.DETAIL) 6 else 3,
                        maxWidth = (if (lod == Lod.DETAIL) 200f else 140f) * density,
                        priority = when {
                            id == selectedId -> 0f
                            ring != null -> 1f
                            else -> 10f + (c - centre).getDistance() / size.maxDimension
                        },
                    )
                }
            }
        }

        // 4. Hubs on top of their spokes, always labelled.
        for (group in visible) {
            val hub = layout.hubPosition(group.id) ?: continue
            val c = at(hub)
            val r = hubPx(group)
            val alpha = alphaOf(group.id)
            drawCircle(colours.hub, r, c, alpha = alpha)
            obstacles += Rect(c.x - r, c.y - r, c.x + r, c.y + r)
            // Under the hub, unless its facts mostly hang below it - then above, clear of them.
            val pull = group.factIds.mapNotNull { layout.factPosition(it) }
                .fold(0f) { acc, p -> acc + (p.y - hub.y) }
            labels += LabelJob(
                text = group.title, node = c, gap = r + 4f * density,
                side = if (pull > 0f && group.factIds.size <= 3) LabelSide.ABOVE else LabelSide.BELOW,
                style = hubStyle, colour = colours.hubLabel, alpha = alpha, maxLines = 1,
                maxWidth = 160f * density, priority = -1f - group.factIds.size / 1000f,
                overNodes = true,
            )
        }

        // 5. Labels last, most important first, each only if it has room.
        drawLabels(labels, obstacles, measurer, density)
    }
}

private data class MapColours(
    val hub: Color, val spoke: Color, val hubLabel: Color, val factLabel: Color,
    val selection: Color, val hit: Color, val fresh: Color,
)

/** Which side of its node a label sits on. */
private enum class LabelSide { ABOVE, BELOW, LEFT, RIGHT }

/** The side a direction (screen space) points to: left or right if it is mostly sideways. */
private fun sideFacing(d: Offset): LabelSide = when {
    kotlin.math.abs(d.x) > kotlin.math.abs(d.y) * 0.8f -> if (d.x > 0) LabelSide.RIGHT else LabelSide.LEFT
    d.y < 0 -> LabelSide.ABOVE
    else -> LabelSide.BELOW
}

/**
 * One label to place: [gap] out from the centre of its [node], on [side]. [overNodes] labels
 * (topic names) may cross dots; fact labels may not.
 */
private data class LabelJob(
    val text: String, val node: Offset, val gap: Float, val side: LabelSide,
    val style: TextStyle, val colour: Color, val alpha: Float,
    val maxLines: Int, val maxWidth: Float, val priority: Float, val overNodes: Boolean = false,
)

private fun factColour(node: GraphNode) = if (node.isDerived) NovaDerived else NovaBlue

/** A label's text laid out to at most [maxWidth] - ellipsised past it. */
private fun measure(measurer: TextMeasurer, job: LabelJob, maxWidth: Float): TextLayoutResult {
    val align = when (job.side) {
        LabelSide.LEFT -> TextAlign.End
        LabelSide.RIGHT -> TextAlign.Start
        else -> TextAlign.Center
    }
    return measurer.measure(
        job.text, job.style.copy(textAlign = align), overflow = TextOverflow.Ellipsis,
        maxLines = job.maxLines, constraints = Constraints(maxWidth = maxWidth.toInt()),
    )
}

/** How wide a label on [job]'s side can be before it runs off the canvas. */
private fun roomFor(job: LabelJob, width: Float, margin: Float): Float {
    val x = job.node.x
    return when (job.side) {
        LabelSide.LEFT -> x - job.gap - margin
        LabelSide.RIGHT -> width - margin - (x + job.gap)
        else -> 2f * minOf(x - margin, width - margin - x)
    }
}

private fun rectFor(job: LabelJob, w: Float, h: Float): Rect {
    val (x, y) = job.node
    return when (job.side) {
        LabelSide.BELOW -> Rect(x - w / 2f, y + job.gap, x + w / 2f, y + job.gap + h)
        LabelSide.ABOVE -> Rect(x - w / 2f, y - job.gap - h, x + w / 2f, y - job.gap)
        LabelSide.RIGHT -> Rect(x + job.gap, y - h / 2f, x + job.gap + w, y + h / 2f)
        LabelSide.LEFT -> Rect(x - job.gap - w, y - h / 2f, x - job.gap, y + h / 2f)
    }
}

/**
 * Greedy, in priority order: a label is drawn only if it clears every label already drawn (and,
 * for facts, every dot) and fits on screen - shortened to the room there is near an edge. One that
 * doesn't is left out rather than clipped or overlapped; it appears once the user zooms or pans to
 * make room for it.
 */
private fun DrawScope.drawLabels(jobs: List<LabelJob>, obstacles: List<Rect>, measurer: TextMeasurer, density: Float) {
    // The screen's zoom-to-fit button sits over the top-right corner; nothing is labelled under it.
    val taken = arrayListOf(Rect(size.width - 56f * density, 0f, size.width, 56f * density))
    val pad = 2f * density
    val margin = 4f * density
    for (job in jobs.sortedBy { it.priority }) {
        // Near an edge, shorter rather than clipped - and only left out if there's barely room.
        val room = minOf(job.maxWidth, roomFor(job, size.width, margin))
        if (room < MIN_LABEL_DP * density) continue
        val laid = measure(measurer, job, room)
        val rect = rectFor(job, laid.size.width.toFloat(), laid.size.height.toFloat())
        if (rect.left < margin || rect.top < margin || rect.right > size.width - margin || rect.bottom > size.height - margin) continue
        val padded = rect.inflate(pad)
        if (taken.any { it.overlaps(padded) }) continue
        if (!job.overNodes && obstacles.any { it.overlaps(padded) }) continue
        taken += padded
        drawText(laid, job.colour, Offset(rect.left, rect.top), alpha = job.alpha)
    }
}
