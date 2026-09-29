package com.example.novav2.knowledge

import com.example.novav2.network.NovaApiClient.KnowledgeGraph
import com.example.novav2.network.NovaApiClient.PersonaSearch

/**
 * The map search bar's result: which facts matched, and the group to fly to.
 *
 * [groups] lists every group holding a hit, best first, so the bar can step between them;
 * [target] is the first. [local] is true when the phone matched words itself because the
 * server has no search yet - by meaning is better, but words still get the user there.
 */
data class MapSearch(
    val query: String,
    val hits: List<Hit>,
    val groups: List<String>,
    val local: Boolean,
) {
    data class Hit(val factId: String, val score: Float, val keyword: Boolean)

    val target: String? get() = groups.firstOrNull()
    val factIds: Set<String> get() = hits.mapTo(HashSet()) { it.factId }
}

/** Words that say nothing about which fact is meant. */
private val STOPWORDS = setOf(
    "a", "an", "the", "i", "my", "me", "is", "are", "was", "do", "does", "did", "to", "of", "in",
    "on", "at", "for", "and", "or", "what", "who", "where", "when", "how", "about", "like", "with",
)

/** Minimum share of the query's words a fact must contain to count as a hit. */
private const val LOCAL_MIN_SCORE = 0.5f

internal fun terms(text: String): List<String> =
    Regex("[a-z0-9]+").findAll(text.lowercase()).map { it.value }
        .filter { it.length >= 2 && it !in STOPWORDS }
        .toList()

/**
 * Word matching over what the phone already holds - the fallback when the server can't search.
 * A query word counts if a word of the fact starts with it ("park" finds "parks", "parking"),
 * and a fact must contain at least half the query's words.
 */
fun localSearch(graph: KnowledgeGraph, query: String): List<MapSearch.Hit> {
    val wanted = terms(query).distinct()
    if (wanted.isEmpty()) return emptyList()
    return graph.nodes.asSequence()
        .filter { it.isFact }
        .mapNotNull { node ->
            val words = terms(listOfNotNull(node.label, node.detail, node.category.joinToString(" ")).joinToString(" "))
            val found = wanted.count { q -> words.any { it.startsWith(q) } }
            val score = found.toFloat() / wanted.size
            if (score >= LOCAL_MIN_SCORE) MapSearch.Hit(node.id, score, keyword = true) else null
        }
        .sortedByDescending { it.score }
        .toList()
}

/**
 * Groups holding hits, best first: the server's own pick leads if it made one; the rest are
 * ordered by the summed score of their hits, ties broken by where the best single hit is.
 */
fun groupsFor(hits: List<MapSearch.Hit>, groupOf: Map<String, String>, preferred: String? = null): List<String> {
    val totals = LinkedHashMap<String, Float>()
    hits.forEach { h -> groupOf[h.factId]?.let { g -> totals[g] = (totals[g] ?: 0f) + h.score } }
    val order = hits.mapNotNull { groupOf[it.factId] }.distinct()
    val ranked = totals.keys.sortedWith(
        compareByDescending<String> { totals.getValue(it) }.thenBy { order.indexOf(it) }
    )
    return if (preferred != null && preferred in totals) listOf(preferred) + (ranked - preferred) else ranked
}

/** A server search result in the map's terms. */
fun fromServer(query: String, result: PersonaSearch, groupOf: Map<String, String>): MapSearch {
    val hits = result.hits
        .filter { it.factId in groupOf }
        .map { MapSearch.Hit(it.factId, it.score, it.keyword) }
    return MapSearch(query, hits, groupsFor(hits, groupOf, result.targetCluster), local = false)
}

/** A local match in the map's terms. */
fun fromLocal(query: String, graph: KnowledgeGraph, groupOf: Map<String, String>): MapSearch {
    val hits = localSearch(graph, query)
    return MapSearch(query, hits, groupsFor(hits, groupOf), local = true)
}

/** fact id -> group id, for the lookups above. */
fun List<MapGroup>.groupIndex(): Map<String, String> =
    buildMap { this@groupIndex.forEach { g -> g.factIds.forEach { put(it, g.id) } } }
