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
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
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
import com.example.novav2.knowledge.provenanceOf
import com.example.novav2.network.NovaApiClient.GraphNode
import com.example.novav2.network.NovaApiClient.KnowledgeGraph
import com.example.novav2.ui.theme.NovaAccentLight
import com.example.novav2.ui.theme.NovaBlue
import com.example.novav2.ui.theme.NovaDerived

/** Everything outside a search's results fades back to this, so the hits stand out. */
private const val DIMMED = 0.3f

/**
 * The map itself: groups under their subheadings, drawn with more detail the further in the
 * user zooms (see [Lod]). Pinch zooms about the fingers, drag pans, tap picks a fact (or, zoomed
 * out, flies into a group). Only what's on screen is drawn or measured.
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
    val measurer = rememberTextMeasurer(cacheSize = 256)
    val nodes = remember(graph) { graph.nodes.associateBy { it.id } }

    // Gestures outlive recompositions; read the latest of these rather than the first.
    val currentCamera by rememberUpdatedState(camera)
    val currentLod by rememberUpdatedState(lod)
    val currentLayout by rememberUpdatedState(layout)
    val currentGroups by rememberUpdatedState(groups)

    val colours = MapColours(
        groupFill = MaterialTheme.colorScheme.surfaceContainer,
        groupBorder = MaterialTheme.colorScheme.outlineVariant,
        heading = MaterialTheme.colorScheme.onSurface,
        muted = MaterialTheme.colorScheme.onSurfaceVariant,
        card = MaterialTheme.colorScheme.surfaceContainerHighest,
        selection = MaterialTheme.colorScheme.onBackground,
        hit = MaterialTheme.colorScheme.primary,
        fresh = MaterialTheme.colorScheme.tertiary,
    )
    val type = MapType(
        groupHeading = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold),
        subheading = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.SemiBold),
        dotLabel = MaterialTheme.typography.labelSmall,
        cardText = MaterialTheme.typography.bodySmall,
        cardMeta = MaterialTheme.typography.labelSmall,
    )

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
                    val world = CameraMath.toWorld(
                        currentCamera, Vec(tap.x - size.width / 2f, tap.y - size.height / 2f), density,
                    )
                    val inGroup = currentGroups.firstOrNull { currentLayout.groupBox(it.id)?.contains(world) == true }
                    if (currentLod == Lod.GROUPS) {
                        if (inGroup != null) onTapGroup(inGroup.id) else onTapFact(null)
                        return@detectTapGestures
                    }
                    val fact = inGroup?.factIds?.firstOrNull { id ->
                        val slot = currentLayout.slots[id] ?: return@firstOrNull false
                        val g = currentLayout.groups[slot.group] ?: return@firstOrNull false
                        MapLayout.cellBox(g, slot.index).contains(world)
                    }
                    onTapFact(fact)
                }
            },
    ) {
        val k = camera.zoom * density
        fun at(w: Vec) = Offset(size.width / 2f + camera.panX + w.x * k, size.height / 2f + camera.panY + w.y * k)
        val view = Box(
            CameraMath.toWorld(camera, Vec(-size.width / 2f, -size.height / 2f), density).x,
            CameraMath.toWorld(camera, Vec(-size.width / 2f, -size.height / 2f), density).y,
            CameraMath.toWorld(camera, Vec(size.width / 2f, size.height / 2f), density).x,
            CameraMath.toWorld(camera, Vec(size.width / 2f, size.height / 2f), density).y,
        )
        val hitGroups = highlight?.groups?.toSet()
        val hitFacts = highlight?.factIds
        val keywordOnly = highlight?.hits?.filter { it.keyword }?.mapTo(HashSet()) { it.factId }.orEmpty()

        if (lod == Lod.DOTS) {
            drawLinks(graph, layout, view, k, ::at, hitFacts)
        }

        for (group in groups) {
            val box = layout.groupBox(group.id) ?: continue
            if (!box.intersects(view)) continue
            val groupAlpha = if (hitGroups != null && group.id !in hitGroups) DIMMED else 1f
            drawGroup(group, box, lod, k, ::at, groupAlpha, measurer, colours, type)
            if (lod == Lod.GROUPS) continue

            for (id in group.factIds) {
                val node = nodes[id] ?: continue
                val slot = layout.slots[id] ?: continue
                val g = layout.groups[slot.group] ?: continue
                val cell = MapLayout.cellBox(g, slot.index)
                if (!cell.intersects(view)) continue
                val mark = FactMark(
                    selected = id == selectedId,
                    hit = hitFacts?.contains(id) == true,
                    keywordHit = id in keywordOnly,
                    fresh = id in newlyLearned,
                    alpha = if (hitFacts != null && id !in hitFacts) DIMMED else 1f,
                )
                if (lod == Lod.CARDS) {
                    drawCard(node, cell, k, ::at, mark, measurer, colours, type)
                } else {
                    drawDot(node, cell, k, ::at, mark, measurer, colours, type)
                }
            }
        }
    }
}

private data class MapColours(
    val groupFill: Color, val groupBorder: Color, val heading: Color, val muted: Color,
    val card: Color, val selection: Color, val hit: Color, val fresh: Color,
)

private data class MapType(
    val groupHeading: TextStyle, val subheading: TextStyle, val dotLabel: TextStyle,
    val cardText: TextStyle, val cardMeta: TextStyle,
)

private data class FactMark(
    val selected: Boolean, val hit: Boolean, val keywordHit: Boolean, val fresh: Boolean, val alpha: Float,
)

private fun factColour(node: GraphNode) = if (node.isDerived) NovaDerived else NovaBlue

/**
 * Text widths change continuously while pinching; rounding them keeps the measurer's cache
 * useful instead of re-laying-out every label on every frame.
 */
private fun quantise(px: Float) = ((px / 8f).toInt() * 8).coerceAtLeast(8)

private fun measure(measurer: TextMeasurer, text: String, style: TextStyle, maxWidth: Float, maxLines: Int, align: TextAlign = TextAlign.Start): TextLayoutResult =
    measurer.measure(
        text, style.copy(textAlign = align), overflow = TextOverflow.Ellipsis, maxLines = maxLines,
        constraints = Constraints(maxWidth = quantise(maxWidth)),
    )

/** A group's block and its subheading - big and centred when zoomed out, above its facts otherwise. */
private fun DrawScope.drawGroup(
    group: MapGroup, box: Box, lod: Lod, k: Float, at: (Vec) -> Offset, alpha: Float,
    measurer: TextMeasurer, colours: MapColours, type: MapType,
) {
    val topLeft = at(Vec(box.left, box.top))
    val size = Size(box.width * k, box.height * k)
    val corner = CornerRadius(16f * k, 16f * k)
    drawRoundRect(colours.groupFill, topLeft, size, corner, alpha = alpha)
    drawRoundRect(colours.groupBorder, topLeft, size, corner, style = Stroke(1.5f), alpha = alpha)

    val pad = 12f * k
    if (lod == Lod.GROUPS) {
        val text = "${group.title} · ${group.factIds.size}"
        if (size.width < 40f) return
        val laid = measure(measurer, text, type.groupHeading, size.width - 2 * pad, 3, TextAlign.Center)
        drawText(
            laid, colours.heading,
            Offset(topLeft.x + (size.width - laid.size.width) / 2f, topLeft.y + (size.height - laid.size.height) / 2f),
            alpha = alpha,
        )
    } else {
        val laid = measure(measurer, group.title, type.subheading, size.width - 2 * pad, 1)
        val headerHeight = MapLayout.HEADER_H * k
        drawText(
            laid, colours.heading,
            Offset(topLeft.x + pad, topLeft.y + (headerHeight - laid.size.height) / 2f),
            alpha = alpha,
        )
    }
}

/** Mid zoom: a dot per fact with as much of its text as fits on one line under it. */
private fun DrawScope.drawDot(
    node: GraphNode, cell: Box, k: Float, at: (Vec) -> Offset, mark: FactMark,
    measurer: TextMeasurer, colours: MapColours, type: MapType,
) {
    val centre = at(Vec(cell.centre.x, cell.centre.y - 10f))
    val radius = (7f + 4f * (node.confidence ?: 1f)) * k
    drawMarks(centre, radius, k, mark, colours)
    drawCircle(factColour(node), radius, centre, alpha = mark.alpha)

    val width = cell.width * k - 8f
    if (width < 36f) return
    val laid = measure(measurer, node.label, type.dotLabel, width, 1, TextAlign.Center)
    drawText(
        laid, colours.muted,
        Offset(centre.x - laid.size.width / 2f, centre.y + radius + 4f),
        alpha = mark.alpha,
    )
}

/** Close up: the fact as a card - what it says, and where it came from. */
private fun DrawScope.drawCard(
    node: GraphNode, cell: Box, k: Float, at: (Vec) -> Offset, mark: FactMark,
    measurer: TextMeasurer, colours: MapColours, type: MapType,
) {
    val inset = 6f
    val topLeft = at(Vec(cell.left + inset, cell.top + inset))
    val size = Size((cell.width - 2 * inset) * k, (cell.height - 2 * inset) * k)
    val corner = CornerRadius(10f * k, 10f * k)

    if (mark.selected || mark.hit || mark.fresh) {
        val ring = when {
            mark.selected -> colours.selection
            mark.hit -> colours.hit
            else -> colours.fresh
        }
        val effect = if (mark.keywordHit && !mark.selected) PathEffect.dashPathEffect(floatArrayOf(10f, 8f)) else null
        drawRoundRect(
            ring, Offset(topLeft.x - 3f, topLeft.y - 3f), Size(size.width + 6f, size.height + 6f),
            CornerRadius(corner.x + 3f, corner.y + 3f), style = Stroke(width = 3f, pathEffect = effect),
        )
    }
    drawRoundRect(colours.card, topLeft, size, corner, alpha = mark.alpha)
    // A stripe in the fact's colour: told (blue) or worked out (teal), as the legend says.
    drawRoundRect(factColour(node), topLeft, Size(4f * k, size.height), CornerRadius(2f * k, 2f * k), alpha = mark.alpha)

    val pad = 10f * k
    val textWidth = size.width - 2 * pad
    if (textWidth < 40f) return
    val meta = measure(measurer, provenanceOf(node), type.cardMeta, textWidth, 1)
    val roomForText = size.height - 2 * pad - meta.size.height
    val lines = (roomForText / (type.cardText.lineHeight.value * density * fontScale)).toInt().coerceIn(1, 3)
    val body = measure(measurer, node.label, type.cardText, textWidth, lines)
    drawText(body, colours.heading, Offset(topLeft.x + pad, topLeft.y + pad), alpha = mark.alpha)
    drawText(
        meta, if (node.isDerived) NovaDerived else colours.muted,
        Offset(topLeft.x + pad, topLeft.y + size.height - pad - meta.size.height),
        alpha = mark.alpha,
    )
}

/** Rings around a dot: selected, a search hit (dashed if it only matched by words), or new. */
private fun DrawScope.drawMarks(centre: Offset, radius: Float, k: Float, mark: FactMark, colours: MapColours) {
    val ring = when {
        mark.selected -> colours.selection
        mark.hit -> colours.hit
        mark.fresh -> colours.fresh
        else -> return
    }
    val effect = if (mark.keywordHit && !mark.selected) PathEffect.dashPathEffect(floatArrayOf(8f, 6f)) else null
    drawCircle(ring, radius + 5f * k, centre, style = Stroke(width = 3f, pathEffect = effect))
}

/** Discovered links between facts, faint - on the dots level only, where there's room for them. */
private fun DrawScope.drawLinks(
    graph: KnowledgeGraph, layout: MapLayout, view: Box, k: Float, at: (Vec) -> Offset, hits: Set<String>?,
) {
    for (edge in graph.edges) {
        if (!edge.isSimilarity) continue
        val a = layout.factPosition(edge.source) ?: continue
        val b = layout.factPosition(edge.target) ?: continue
        if (!view.contains(a) && !view.contains(b)) continue
        val lit = hits != null && (edge.source in hits || edge.target in hits)
        val alpha = (0.15f + (edge.weight - 0.6f)).coerceIn(0.1f, 0.5f) * if (hits != null && !lit) DIMMED else 1f
        drawLine(
            NovaAccentLight.copy(alpha = alpha), at(Vec(a.x, a.y - 10f)), at(Vec(b.x, b.y - 10f)),
            strokeWidth = (1.5f * k).coerceIn(1f, 3f),
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(9f, 7f)),
        )
    }
}
