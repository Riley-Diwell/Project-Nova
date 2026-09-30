package com.example.novav2.knowledge

import com.example.novav2.network.NovaApiClient.GraphNode
import com.example.novav2.network.NovaApiClient.KnowledgeGraph

/**
 * One subheading on the Knowledge Map and the facts under it.
 *
 * Where the groups come from depends on the server:
 *  - one that groups by meaning sends "cluster" nodes, each with a heading, and every fact names
 *    its cluster. Those are used as they are - the server keeps them stable, so the map doesn't
 *    reorganise itself in front of the user.
 *  - one that doesn't yet (today's) only sends the category skeleton, so facts are grouped by
 *    the first two levels of their category ("Opinions › Likes").
 *
 * [synthetic] marks a group the phone made up rather than one the server named.
 */
data class MapGroup(
    val id: String,
    val title: String,
    val factIds: List<String>,
    val synthetic: Boolean = false,
)

/** Facts a meaning-grouping server hasn't placed yet (it places them on its next pass). */
const val RECENT_GROUP_ID = "group:recent"
private const val CATEGORY_DEPTH = 2

/** Every fact in [graph], each in exactly one group, in a stable order. */
fun groupsOf(graph: KnowledgeGraph): List<MapGroup> {
    val facts = graph.nodes.filter { it.isFact }
    val clusters = graph.nodes.filter { it.isCluster }
    return if (clusters.isNotEmpty()) byCluster(facts, clusters) else byCategory(facts)
}

private fun byCluster(facts: List<GraphNode>, clusters: List<GraphNode>): List<MapGroup> {
    val known = clusters.map { it.id }.toSet()
    val members = facts.groupBy { f -> f.clusterId?.takeIf { it in known } ?: RECENT_GROUP_ID }
    val groups = clusters.mapNotNull { c ->
        members[c.id]?.let { MapGroup(c.id, c.label.ifBlank { "Untitled" }, it.map(GraphNode::id)) }
    }
    val recent = members[RECENT_GROUP_ID]?.let {
        MapGroup(RECENT_GROUP_ID, "Recently learned", it.map(GraphNode::id), synthetic = true)
    }
    return groups + listOfNotNull(recent)
}

/** What pulls nodes together in the layout: related facts, and facts to the other topics they
 *  are also about. */
fun linksOf(graph: KnowledgeGraph): List<Link> = graph.edges.mapNotNull { e ->
    when {
        e.isSimilarity -> Link(e.source, e.target, e.weight, Link.Kind.SIMILAR)
        e.isTopicLink -> Link(e.source, e.target, e.weight, Link.Kind.TOPIC)
        else -> null
    }
}

private fun byCategory(facts: List<GraphNode>): List<MapGroup> =
    facts.groupBy { it.category.take(CATEGORY_DEPTH) }
        .map { (path, members) ->
            MapGroup(
                id = if (path.isEmpty()) "cat:" else "cat:" + path.joinToString("/"),
                title = if (path.isEmpty()) "Other" else path.joinToString(" › ") { it.titleCase() },
                factIds = members.map(GraphNode::id),
                synthetic = true,
            )
        }
        .sortedBy { it.id }

private fun String.titleCase(): String =
    replace('_', ' ').replaceFirstChar { if (it.isLowerCase()) it.titlecase() else it.toString() }

/**
 * The one-line "where this came from" shown under a fact on the map's close-up cards, so the
 * user can read what a belief rests on without opening it:
 *   "You told NOVA · 3 Sep", "Seen 5 times · last 12 Sep", "From your note".
 */
fun provenanceOf(node: GraphNode): String {
    val date = node.statedAt?.let(::shortDate)
    return when {
        node.isDerived -> listOfNotNull(
            node.support?.let { "Seen $it times" } ?: "NOVA worked this out",
            date?.let { "last $it" },
        ).joinToString(" · ")
        node.noteId != null -> listOfNotNull("From your note", date).joinToString(" · ")
        else -> listOfNotNull("You told NOVA", date).joinToString(" · ")
    }
}

private val MONTHS = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

/**
 * An ISO 8601 instant from the server ("2026-09-29T10:00:00.123456+00:00", or "...Z") as epoch
 * millis, or null. SimpleDateFormat rather than java.time, which needs API 26 (minSdk is 24);
 * it can't read Python's microseconds, so the fraction is dropped first.
 */
fun isoToMillis(iso: String): Long? {
    val cleaned = iso.trim()
        .replace(Regex("""\.\d+"""), "")
        .replace(Regex("""Z$"""), "+00:00")
    return try {
        java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", java.util.Locale.US).parse(cleaned)?.time
    } catch (e: java.text.ParseException) {
        null
    } catch (e: IllegalArgumentException) {
        null
    }
}

/** "just now", "12m ago", "3h ago", "2d ago" - for the map's "Updated …" line. */
fun agoText(thenMillis: Long, nowMillis: Long): String {
    val minutes = (nowMillis - thenMillis).coerceAtLeast(0) / 60_000
    return when {
        minutes < 1 -> "just now"
        minutes < 60 -> "${minutes}m ago"
        minutes < 60 * 24 -> "${minutes / 60}h ago"
        else -> "${minutes / (60 * 24)}d ago"
    }
}

/** "2026-09-03T10:00:00+00:00" -> "3 Sep". By hand: java.time needs API 26 and minSdk is 24. */
internal fun shortDate(iso: String): String? {
    val match = Regex("""^(\d{4})-(\d{2})-(\d{2})""").find(iso) ?: return null
    val (_, month, day) = match.destructured
    val m = month.toIntOrNull()?.takeIf { it in 1..12 } ?: return null
    val d = day.toIntOrNull() ?: return null
    return "$d ${MONTHS[m - 1]}"
}
