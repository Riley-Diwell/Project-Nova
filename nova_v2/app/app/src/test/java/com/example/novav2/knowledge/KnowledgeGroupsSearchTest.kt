package com.example.novav2.knowledge

import com.example.novav2.network.NovaApiClient
import com.example.novav2.network.NovaApiClient.GraphNode
import com.example.novav2.network.NovaApiClient.KnowledgeGraph
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class KnowledgeGroupsSearchTest {
    private fun fact(id: String, label: String, vararg path: String, cluster: String? = null, source: String = "stated") =
        GraphNode(id = id, label = label, kind = "fact", source = source, category = path.toList(), clusterId = cluster)

    // Today's server: facts with category paths, no clusters.
    private val categorised = KnowledgeGraph(nodes = listOf(
        GraphNode("cat:opinions", "opinions", "category", category = listOf("opinions")),
        fact("pizza", "Likes pizza", "opinions", "likes", "food"),
        fact("sushi", "Likes sushi", "opinions", "likes", "food"),
        fact("bus", "Takes the bus to uni", "routines", "travel"),
        fact("gym", "Parks at the gym car park", "routines", "places", source = "derived"),
        fact("loose", "Has a sister called Maya"),
    ))

    @Test
    fun `without clusters, facts are grouped by the first two levels of their category`() {
        val groups = groupsOf(categorised).associate { it.title to it.factIds }
        assertEquals(listOf("pizza", "sushi"), groups["Opinions › Likes"])
        assertEquals(listOf("bus"), groups["Routines › Travel"])
        assertEquals(listOf("loose"), groups["Other"])
        assertEquals(5, groupsOf(categorised).sumOf { it.factIds.size })
    }

    @Test
    fun `with clusters, the server's groups and headings are used, and stragglers go to recent`() {
        val graph = KnowledgeGraph(nodes = listOf(
            GraphNode("cluster:a", "Food likes", "cluster", size = 2),
            GraphNode("cluster:b", "Getting to uni", "cluster", size = 1),
            fact("pizza", "Likes pizza", cluster = "cluster:a"),
            fact("sushi", "Likes sushi", cluster = "cluster:a"),
            fact("bus", "Takes the bus", cluster = "cluster:b"),
            fact("new", "Just said this", cluster = null),
        ))
        val groups = groupsOf(graph)
        assertEquals(listOf("Food likes", "Getting to uni", "Recently learned"), groups.map { it.title })
        assertEquals(listOf("new"), groups.last().factIds)
    }

    @Test
    fun `local search matches word starts and needs half the words`() {
        val hits = localSearch(categorised, "parking at the gym")
        assertEquals(listOf("gym"), hits.map { it.factId })
        assertTrue(hits.all { it.keyword })
        assertTrue(localSearch(categorised, "what about").isEmpty()) // stopwords only
    }

    @Test
    fun `the target group is the server's pick, otherwise where the hits add up`() {
        val groupOf = mapOf("pizza" to "food", "sushi" to "food", "bus" to "travel")
        val hits = listOf(
            MapSearch.Hit("bus", 0.9f, false),
            MapSearch.Hit("pizza", 0.6f, false),
            MapSearch.Hit("sushi", 0.5f, false),
        )
        assertEquals(listOf("food", "travel"), groupsFor(hits, groupOf))
        assertEquals(listOf("travel", "food"), groupsFor(hits, groupOf, preferred = "travel"))
    }

    @Test
    fun `server search results come back in the map's terms`() {
        val result = NovaApiClient.parsePersonaSearch(JSONObject("""
            {"hits": [{"fact_id": "bus", "score": 0.03, "similarity": 0.7, "match": "meaning", "cluster_id": "b"},
                      {"fact_id": "gone", "score": 0.01, "similarity": 0.6, "match": "keyword", "cluster_id": null}],
             "target_cluster": "b"}
        """))
        assertEquals("cluster:b", result.targetCluster)
        val search = fromServer("uni", result, mapOf("bus" to "cluster:b"))
        assertEquals(listOf("bus"), search.hits.map { it.factId }) // not on the map: dropped
        assertEquals("cluster:b", search.target)
    }

    @Test
    fun `provenance reads as a short line`() {
        assertEquals("You told NOVA · 3 Sep", provenanceOf(fact("a", "x").copy(statedAt = "2026-09-03T10:00:00+00:00")))
        assertEquals("Seen 5 times · last 12 Sep",
            provenanceOf(fact("b", "x", source = "derived").copy(support = 5, statedAt = "2026-09-12T08:00:00Z")))
        assertEquals("From your note", provenanceOf(fact("c", "x").copy(noteId = "n1")))
        assertEquals("You told NOVA", provenanceOf(fact("d", "x")))
    }

    @Test
    fun `server timestamps parse, microseconds and all`() {
        assertEquals(1_790_157_600_000L, isoToMillis("2026-09-23T10:00:00.123456+00:00"))
        assertEquals(1_790_157_600_000L, isoToMillis("2026-09-23T10:00:00Z"))
        assertNull(isoToMillis("not a date"))
        assertEquals("3h ago", agoText(0L, 3 * 3_600_000L + 5))
    }

    @Test
    fun `graphs from older servers parse, and new fields round-trip`() {
        val old = NovaApiClient.parseKnowledgeGraph(JSONObject(
            """{"nodes": [{"id": "a", "label": "Likes pizza", "kind": "fact", "source": "stated"}], "edges": []}"""
        ))
        assertNull(old.nodes.single().clusterId)
        assertNull(old.nodes.single().statedAt)

        val new = KnowledgeGraph(nodes = listOf(
            GraphNode("cluster:a", "Food", "cluster", size = 3),
            fact("f", "Likes pizza", cluster = "cluster:a").copy(statedAt = "2026-09-03T10:00:00+00:00"),
        ))
        val back = NovaApiClient.parseKnowledgeGraph(with(NovaApiClient) { new.toJson() })
        assertEquals(new, back)
    }
}
