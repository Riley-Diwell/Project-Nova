package com.example.novav2.knowledge

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Where everything sits on the Knowledge Map, in world units (dp at zoom 1).
 *
 * THE SHAPE
 * A graph, like Obsidian's: each topic is a hub with its facts around it, and how far apart
 * two things sit says how related they are. It is a force simulation -
 *  - every fact is held to its own topic's hub, and more loosely to any other topic it is also
 *    about (the server's "topic" links);
 *  - related facts pull together, the more alike the closer - wherever their topics are, so a
 *    fact about food at bedtime drifts towards Sleep;
 *  - everything pushes everything else apart, so nothing lands on top of anything.
 *
 * STABLE ON PURPOSE
 * A map the user has learned to find their way round must not reshuffle itself. Anything that
 * already has a place is held to it by a spring of its own, so a new fact settles in among the
 * rest and they barely move to make room. Only a first layout (nothing placed yet) is free to
 * arrange everything - it's the one the user hasn't seen.
 *
 * Deterministic: the same groups, links and previous layout always give the same result.
 * Quadratic in the number of nodes per step, so it runs off the main thread (KnowledgeRepository).
 */
data class Vec(val x: Float, val y: Float) {
    operator fun plus(o: Vec) = Vec(x + o.x, y + o.y)
    operator fun minus(o: Vec) = Vec(x - o.x, y - o.y)
    operator fun times(k: Float) = Vec(x * k, y * k)
    fun distanceTo(o: Vec): Float = sqrt((x - o.x) * (x - o.x) + (y - o.y) * (y - o.y))
}

data class Box(val left: Float, val top: Float, val right: Float, val bottom: Float) {
    val width get() = right - left
    val height get() = bottom - top
    val centre get() = Vec((left + right) / 2f, (top + bottom) / 2f)
    fun inflate(by: Float) = Box(left - by, top - by, right + by, bottom + by)
    fun intersects(o: Box) = left < o.right && o.left < right && top < o.bottom && o.top < bottom
    fun contains(p: Vec) = p.x in left..right && p.y in top..bottom
    fun union(o: Box) = Box(min(left, o.left), min(top, o.top), max(right, o.right), max(bottom, o.bottom))

    companion object {
        fun around(c: Vec, r: Float) = Box(c.x - r, c.y - r, c.x + r, c.y + r)
    }
}

/** A pull between two nodes from the graph: two related facts, or a fact and another topic. */
data class Link(val a: String, val b: String, val weight: Float, val kind: Kind) {
    enum class Kind { SIMILAR, TOPIC }
}

data class MapLayout(
    val hubs: Map<String, Vec> = emptyMap(),
    val facts: Map<String, Vec> = emptyMap(),
    /** Each hub's own facts, for [groupBox]. */
    val members: Map<String, List<String>> = emptyMap(),
) {
    fun hubPosition(id: String): Vec? = hubs[id]

    fun factPosition(id: String): Vec? = facts[id]

    /** A hub and its facts, labels included - for zoom-to-fit and culling. Never narrower than
     *  a hub's own name, so zoom-to-fit can't cut a label off at the edge. */
    fun groupBox(id: String): Box? {
        val hub = hubs[id] ?: return null
        var box = Box(hub.x - MIN_HALF_WIDTH, hub.y - LABEL_ROOM, hub.x + MIN_HALF_WIDTH, hub.y + LABEL_ROOM)
        members[id].orEmpty().forEach { f -> facts[f]?.let { box = box.union(Box.around(it, LABEL_ROOM)) } }
        return box
    }

    /** Everything, for zoom-to-fit. Null for an empty map. */
    fun bounds(): Box? = hubs.keys.mapNotNull(::groupBox).reduceOrNull(Box::union)

    companion object {
        /** Room kept round a node for its label. */
        const val LABEL_ROOM = 44f
        /** Half the width a hub's name can need - see [groupBox]. */
        const val MIN_HALF_WIDTH = 84f

        /** Visual size of a hub, growing gently with its topic. */
        fun hubRadius(facts: Int) = 12f + 2.4f * sqrt(facts.toFloat())

        const val FACT_RADIUS = 6f
    }
}

/**
 * Lays out [groups] and the [links] between their facts and topics, starting from [previous] and
 * holding what it placed where it was. The same inputs always give the same layout.
 */
fun layoutOf(groups: List<MapGroup>, links: List<Link> = emptyList(), previous: MapLayout? = null): MapLayout {
    val before = previous ?: MapLayout()
    if (groups.isEmpty()) return MapLayout()

    val home = HashMap<String, String>()
    groups.forEach { g -> g.factIds.forEach { home[it] = g.id } }

    // --- where each node starts ---
    val nodes = ArrayList<Node>()
    val index = HashMap<String, Int>()
    fun add(id: String, hub: Boolean, at: Vec, anchor: Vec?) {
        index[id] = nodes.size
        nodes += Node(id, hub, at.x, at.y, anchor)
    }

    val placedHubs = ArrayList<Vec>()
    for (g in groups) {
        val was = before.hubs[g.id]
        val start = was
            // A new topic whose facts were already on the map (they moved into it): among them.
            ?: g.factIds.mapNotNull { before.facts[it] }.takeIf { it.isNotEmpty() }?.let(::centroid)
            ?: freeSpot(placedHubs + before.hubs.values)
        placedHubs += start
        add(g.id, hub = true, at = start, anchor = was)
    }
    for (g in groups) {
        val hub = nodes[index.getValue(g.id)]
        for (f in g.factIds) {
            val was = before.facts[f]?.takeIf { before.members[g.id]?.contains(f) == true }
            // New here: beside its hub, in a direction of its own so new facts don't stack.
            val start = was ?: Vec(hub.x, hub.y) + unit(angleOf(f)) * SPOKE
            add(f, hub = false, at = start, anchor = was)
        }
    }

    // --- the forces ---
    val springs = ArrayList<Spring>()
    for (g in groups) for (f in g.factIds) springs += Spring(index.getValue(f), index.getValue(g.id), SPOKE, 0.08f)
    for (link in links) {
        val a = index[link.a] ?: continue
        val b = index[link.b] ?: continue
        when (link.kind) {
            Link.Kind.SIMILAR -> {
                val s = ((link.weight - SIMILAR_FLOOR) / (SIMILAR_CEILING - SIMILAR_FLOOR)).coerceIn(0f, 1f)
                springs += Spring(a, b, rest = 160f - 110f * s, k = 0.01f + 0.05f * s)
            }
            Link.Kind.TOPIC -> {
                val s = ((link.weight - 0.6f) / 0.3f).coerceIn(0.2f, 1f)
                springs += Spring(a, b, rest = 150f, k = 0.02f + 0.05f * s)
            }
        }
    }

    val fresh = nodes.none { it.anchor != null }
    val n = nodes.size
    val iterations = (if (fresh) 400 else 160).coerceAtMost(max(40, 30_000_000 / max(1, n * n)))
    val fx = FloatArray(n)
    val fy = FloatArray(n)
    for (step in 0 until iterations) {
        fx.fill(0f); fy.fill(0f)
        // Everything apart, hubs hardest.
        for (i in 0 until n) for (j in i + 1 until n) {
            val a = nodes[i]; val b = nodes[j]
            var dx = a.x - b.x; var dy = a.y - b.y
            if (abs(dx) > CUTOFF || abs(dy) > CUTOFF) continue
            var d2 = dx * dx + dy * dy
            if (d2 < 1e-4f) { dx = (i - j).toFloat() * 0.01f; dy = 0.01f; d2 = dx * dx + dy * dy }
            val d = sqrt(d2)
            val c = when {
                a.hub && b.hub -> 40_000f
                a.hub || b.hub -> 5_000f
                else -> 1_800f
            }
            val f = c / max(d2, 100f)
            fx[i] += f * dx / d; fy[i] += f * dy / d
            fx[j] -= f * dx / d; fy[j] -= f * dy / d
        }
        for (s in springs) {
            val a = nodes[s.a]; val b = nodes[s.b]
            val dx = b.x - a.x; val dy = b.y - a.y
            val d = max(sqrt(dx * dx + dy * dy), 0.01f)
            val f = s.k * (d - s.rest)
            fx[s.a] += f * dx / d; fy[s.a] += f * dy / d
            fx[s.b] -= f * dx / d; fy[s.b] -= f * dy / d
        }
        for (i in 0 until n) {
            val node = nodes[i]
            node.anchor?.let { fx[i] += (it.x - node.x) * ANCHOR; fy[i] += (it.y - node.y) * ANCHOR }
            fx[i] -= node.x * GRAVITY; fy[i] -= node.y * GRAVITY
        }
        // Cooling: big moves early, settling late.
        val limit = 30f * (1f - step.toFloat() / iterations) + 0.5f
        for (i in 0 until n) {
            val len = sqrt(fx[i] * fx[i] + fy[i] * fy[i])
            val scale = if (len > limit) limit / len else 1f
            nodes[i].x += fx[i] * scale
            nodes[i].y += fy[i] * scale
        }
    }
    separate(nodes)

    val hubs = LinkedHashMap<String, Vec>()
    val facts = HashMap<String, Vec>()
    nodes.forEach { if (it.hub) hubs[it.id] = Vec(it.x, it.y) else facts[it.id] = Vec(it.x, it.y) }
    return MapLayout(hubs, facts, groups.associate { it.id to it.factIds })
}

private class Node(val id: String, val hub: Boolean, var x: Float, var y: Float, val anchor: Vec?)

private class Spring(val a: Int, val b: Int, val rest: Float, val k: Float)

/** Pushes apart anything still closer than its minimum - the forces leave a little overlap. */
private fun separate(nodes: List<Node>) {
    repeat(6) {
        for (i in nodes.indices) for (j in i + 1 until nodes.size) {
            val a = nodes[i]; val b = nodes[j]
            val min = when {
                a.hub && b.hub -> 150f
                a.hub || b.hub -> 40f
                else -> 30f
            }
            val dx = b.x - a.x; val dy = b.y - a.y
            val d = sqrt(dx * dx + dy * dy)
            if (d >= min) continue
            val ux = if (d > 1e-3f) dx / d else 1f
            val uy = if (d > 1e-3f) dy / d else 0f
            val push = (min - d) / 2f
            a.x -= ux * push; a.y -= uy * push
            b.x += ux * push; b.y += uy * push
        }
    }
}

private fun centroid(points: List<Vec>) = Vec(points.map { it.x }.average().toFloat(), points.map { it.y }.average().toFloat())

private fun unit(angle: Double) = Vec(cos(angle).toFloat(), sin(angle).toFloat())

/** A direction fixed per id, so the same fact always starts the same way. */
private fun angleOf(id: String): Double = (id.hashCode().toLong() and 0xffff) / 65536.0 * 2 * PI

/** The first spot on a spiral out from the origin clear of every hub already placed. */
private fun freeSpot(taken: Collection<Vec>): Vec {
    if (taken.isEmpty()) return Vec(0f, 0f)
    var angle = 0.0
    repeat(4000) {
        val radius = 140.0 * angle / (2 * PI)
        val c = Vec((radius * cos(angle)).toFloat(), (radius * sin(angle) * 1.25).toFloat())
        if (taken.all { it.distanceTo(c) >= HUB_SPACING }) return c
        angle += 0.35 / max(1.0, radius / 140.0)
    }
    return Vec(0f, taken.maxOf { it.y } + HUB_SPACING)
}

/** A fact's resting distance from its own hub. */
private const val SPOKE = 80f
/** Cosine at which two facts start to pull together, and at which they pull hardest. */
private const val SIMILAR_FLOOR = 0.55f
private const val SIMILAR_CEILING = 0.75f
/** How hard an already-placed node is held to where it was. */
private const val ANCHOR = 0.06f
/** A faint pull to the middle, so unconnected topics don't drift off. */
private const val GRAVITY = 0.004f
/** Beyond this apart (either axis), two nodes don't push each other. */
private const val CUTOFF = 600f
private const val HUB_SPACING = 260f
