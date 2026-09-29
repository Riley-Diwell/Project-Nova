package com.example.novav2.knowledge

import kotlin.math.PI
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Where everything sits on the Knowledge Map, in world units (dp at zoom 1).
 *
 * WHY NOT A FORCE SIMULATION
 * The old map re-ran a physics layout whenever the graph changed, seeded from the node count -
 * so learning one new fact could shuffle everything the user had learned to find. This layout
 * is incremental instead, and the rule is simple: nothing that has a place ever moves.
 *  - A group (a subheading) keeps its position forever. A new group goes in the first free
 *    space on a spiral out from the middle; a group that disappears leaves a gap.
 *  - Inside a group, facts sit in a grid of cells and a fact keeps its cell. A new fact takes
 *    the lowest free cell, so removing one leaves a gap rather than shifting its neighbours.
 *  - Each group reserves room to grow downwards when it is placed, so a growing group doesn't
 *    run into the one below it.
 *
 * A grid rather than scattered dots is also what lets the close-up zoom show readable cards:
 * cells can't overlap, so neither can the text in them.
 *
 * Pure Kotlin, deterministic, and linear in the number of facts (plus a short spiral search per
 * new group), so it runs on every graph change without a background thread.
 */
data class Vec(val x: Float, val y: Float) {
    operator fun plus(o: Vec) = Vec(x + o.x, y + o.y)
    operator fun minus(o: Vec) = Vec(x - o.x, y - o.y)
    operator fun times(k: Float) = Vec(x * k, y * k)
}

data class Box(val left: Float, val top: Float, val right: Float, val bottom: Float) {
    val width get() = right - left
    val height get() = bottom - top
    val centre get() = Vec((left + right) / 2f, (top + bottom) / 2f)
    fun inflate(by: Float) = Box(left - by, top - by, right + by, bottom + by)
    fun intersects(o: Box) = left < o.right && o.left < right && top < o.bottom && o.top < bottom
    fun contains(p: Vec) = p.x in left..right && p.y in top..bottom
    fun union(o: Box) = Box(min(left, o.left), min(top, o.top), max(right, o.right), max(bottom, o.bottom))
}

/** A group's fixed place: its top-left corner, its column count and the rows it reserved. */
data class GroupSlot(val x: Float, val y: Float, val cols: Int, val reservedRows: Int)

/** Which group a fact sits in and which cell of that group's grid. */
data class FactSlot(val group: String, val index: Int)

data class MapLayout(
    val groups: Map<String, GroupSlot> = emptyMap(),
    val slots: Map<String, FactSlot> = emptyMap(),
) {
    /** Rows each group currently fills - from its highest occupied cell, gaps included. */
    val rows: Map<String, Int> by lazy {
        val highest = HashMap<String, Int>()
        slots.values.forEach { s -> highest[s.group] = max(highest[s.group] ?: -1, s.index) }
        groups.mapValues { (id, g) -> rowsFor((highest[id] ?: -1) + 1, g.cols) }
    }

    fun groupBox(id: String): Box? {
        val g = groups[id] ?: return null
        return boxOf(g, max(1, rows[id] ?: 1))
    }

    fun factPosition(id: String): Vec? {
        val slot = slots[id] ?: return null
        val g = groups[slot.group] ?: return null
        return cellCentre(g, slot.index)
    }

    /** Everything, for zoom-to-fit. Null for an empty map. */
    fun bounds(): Box? = groups.keys.mapNotNull(::groupBox).reduceOrNull(Box::union)

    companion object {
        const val CELL_W = 176f
        const val CELL_H = 88f
        /** Room above a group's grid for its subheading. */
        const val HEADER_H = 44f
        const val PAD = 12f
        /** Clear space kept between groups. */
        const val GAP = 64f
        const val MAX_COLS = 5

        fun cellCentre(g: GroupSlot, index: Int): Vec {
            val col = index % g.cols
            val row = index / g.cols
            return Vec(g.x + PAD + (col + 0.5f) * CELL_W, g.y + HEADER_H + (row + 0.5f) * CELL_H)
        }

        /** The cell's own rectangle, for drawing a card in it. */
        fun cellBox(g: GroupSlot, index: Int): Box {
            val c = cellCentre(g, index)
            return Box(c.x - CELL_W / 2f, c.y - CELL_H / 2f, c.x + CELL_W / 2f, c.y + CELL_H / 2f)
        }

        fun boxOf(g: GroupSlot, rows: Int) =
            Box(g.x, g.y, g.x + 2 * PAD + g.cols * CELL_W, g.y + HEADER_H + rows * CELL_H + PAD)
    }
}

internal fun rowsFor(cells: Int, cols: Int): Int = if (cells <= 0) 0 else (cells + cols - 1) / cols

/** Wide enough to read as a block rather than a column, never wider than [MapLayout.MAX_COLS]. */
internal fun colsFor(n: Int): Int = max(1, minOf(n, MapLayout.MAX_COLS, ceil(sqrt(1.6 * n)).toInt()))

/** Rows kept free under a new group so it can grow without meeting its neighbour. */
internal fun reserveFor(rows: Int): Int = rows + max(2, (rows + 1) / 2)

/**
 * Lays out [groups], keeping every position [previous] already gave out. The same inputs
 * always give the same layout.
 */
fun layoutOf(groups: List<MapGroup>, previous: MapLayout? = null): MapLayout {
    val before = previous ?: MapLayout()
    val present = groups.associateBy { it.id }

    // Groups already placed keep their place. Groups that went are dropped, freeing their space.
    val placed = LinkedHashMap<String, GroupSlot>()
    before.groups.forEach { (id, slot) -> if (id in present) placed[id] = slot }

    // New groups: biggest first, so the first layout puts the main topics in the middle.
    val fresh = groups.filter { it.id !in placed }
        .sortedWith(compareByDescending<MapGroup> { it.factIds.size }.thenBy { it.id })
    for (group in fresh) {
        val cols = colsFor(group.factIds.size)
        val reserved = reserveFor(rowsFor(group.factIds.size, cols))
        val occupied = placed.map { (id, g) -> MapLayout.boxOf(g, max(g.reservedRows, before.rows[id] ?: 0)) }
        placed[group.id] = placeGroup(cols, reserved, occupied)
    }

    // Facts keep their cell while they stay in the same group; others take the lowest free one.
    val slots = HashMap<String, FactSlot>()
    for (group in groups) {
        val taken = HashSet<Int>()
        val waiting = ArrayList<String>()
        for (id in group.factIds) {
            val was = before.slots[id]
            if (was != null && was.group == group.id && taken.add(was.index)) {
                slots[id] = was
            } else {
                waiting += id
            }
        }
        var next = 0
        for (id in waiting) {
            while (next in taken) next++
            taken += next
            slots[id] = FactSlot(group.id, next)
        }
    }
    return MapLayout(placed, slots)
}

/** The first spot on a spiral out from the origin where a group this size fits clear of the rest. */
private fun placeGroup(cols: Int, reservedRows: Int, occupied: List<Box>): GroupSlot {
    val size = MapLayout.boxOf(GroupSlot(0f, 0f, cols, reservedRows), reservedRows)
    val taken = occupied.map { it.inflate(MapLayout.GAP / 2f) }

    fun at(centre: Vec) = GroupSlot(centre.x - size.width / 2f, centre.y - size.height / 2f, cols, reservedRows)
    fun fits(g: GroupSlot) = MapLayout.boxOf(g, reservedRows).inflate(MapLayout.GAP / 2f).let { box ->
        taken.none { it.intersects(box) }
    }

    if (taken.isEmpty()) return at(Vec(0f, 0f))
    var angle = 0.0
    repeat(SPIRAL_TRIES) {
        // Archimedean spiral, a little flatter than a circle to suit a portrait screen's width.
        val radius = SPIRAL_PITCH * angle / (2 * PI)
        val candidate = at(Vec((radius * cos(angle)).toFloat(), (radius * sin(angle) * 1.2).toFloat()))
        if (fits(candidate)) return candidate
        angle += SPIRAL_STEP / max(1.0, radius / SPIRAL_PITCH)
    }
    // Unreachable in practice; below everything is always free.
    val bottom = taken.maxOf { it.bottom }
    return GroupSlot(-size.width / 2f, bottom + MapLayout.GAP, cols, reservedRows)
}

private const val SPIRAL_PITCH = 120.0
private const val SPIRAL_STEP = 0.5
private const val SPIRAL_TRIES = 6000
