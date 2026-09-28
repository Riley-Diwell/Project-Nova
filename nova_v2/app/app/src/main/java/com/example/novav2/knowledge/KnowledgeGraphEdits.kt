package com.example.novav2.knowledge

import com.example.novav2.network.NovaApiClient.KnowledgeGraph

// The user's own changes applied to a graph on the phone, so the map needn't be refetched for
// them ([KnowledgeRepository]).

/** A correction makes a belief something the user stated - the server does the same. */
internal fun KnowledgeGraph.relabelled(labels: Map<String, String>): KnowledgeGraph {
    if (labels.isEmpty()) return this
    return copy(nodes = nodes.map { n ->
        labels[n.id]?.let { n.copy(label = it, source = "stated") } ?: n
    })
}

/**
 * The graph without [factIds], their links, and any category left with nothing filed under
 * it. Category ids are "cat:" + the path joined with "/" (server persona/graph.py), so the
 * categories still needed are exactly the path prefixes of the facts that remain.
 */
internal fun KnowledgeGraph.without(factIds: Set<String>): KnowledgeGraph {
    if (factIds.isEmpty()) return this
    val remaining = nodes.filter { it.isFact && it.id !in factIds }
    val neededCategories = remaining.flatMapTo(HashSet()) { fact ->
        fact.category.indices.map { depth -> "cat:" + fact.category.take(depth + 1).joinToString("/") }
    }
    val kept = nodes.filter { if (it.isFact) it.id !in factIds else it.id in neededCategories }
    val keptIds = kept.mapTo(HashSet()) { it.id }
    return KnowledgeGraph(
        nodes = kept,
        edges = edges.filter { it.source in keptIds && it.target in keptIds },
    )
}
