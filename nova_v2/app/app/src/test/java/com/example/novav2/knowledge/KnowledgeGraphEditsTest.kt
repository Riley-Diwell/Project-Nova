package com.example.novav2.knowledge

import com.example.novav2.network.NovaApiClient.GraphEdge
import com.example.novav2.network.NovaApiClient.GraphNode
import com.example.novav2.network.NovaApiClient.KnowledgeGraph
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test

class KnowledgeGraphEditsTest {
    // Shaped like the server's build_graph: a category chain per fact, the fact off its leaf.
    private fun fact(id: String, vararg path: String, noteId: String? = null) =
        GraphNode(id = id, label = "fact $id", kind = "fact", source = "derived", category = path.toList(), noteId = noteId)

    private fun category(vararg path: String) =
        GraphNode(id = "cat:" + path.joinToString("/"), label = path.last(), kind = "category", category = path.toList())

    private fun link(a: String, b: String, kind: String = "category") = GraphEdge(a, b, kind, 1f)

    // opinions > likes > food: pizza, sushi.   routines > places: gym.   sushi ~ gym (similar).
    private val graph = KnowledgeGraph(
        nodes = listOf(
            category("opinions"), category("opinions", "likes"), category("opinions", "likes", "food"),
            fact("pizza", "opinions", "likes", "food"),
            fact("sushi", "opinions", "likes", "food"),
            category("routines"), category("routines", "places"),
            fact("gym", "routines", "places", noteId = "n1"),
        ),
        edges = listOf(
            link("cat:opinions", "cat:opinions/likes"),
            link("cat:opinions/likes", "cat:opinions/likes/food"),
            link("cat:opinions/likes/food", "pizza"),
            link("cat:opinions/likes/food", "sushi"),
            link("cat:routines", "cat:routines/places"),
            link("cat:routines/places", "gym"),
            link("sushi", "gym", kind = "similar"),
        ),
    )

    private fun KnowledgeGraph.ids() = nodes.map { it.id }.toSet()

    @Test
    fun `forgetting a fact drops it and its links but keeps a shared category`() {
        val after = graph.without(setOf("sushi"))
        assertEquals(graph.ids() - "sushi", after.ids())
        assertEquals(graph.edges.filter { "sushi" !in listOf(it.source, it.target) }, after.edges)
    }

    @Test
    fun `forgetting the last fact under a branch drops the branch`() {
        val after = graph.without(setOf("gym"))
        assertEquals(graph.ids() - setOf("gym", "cat:routines", "cat:routines/places"), after.ids())
        assertEquals(4, after.edges.size)
    }

    @Test
    fun `forgetting everything leaves an empty graph`() {
        val after = graph.without(setOf("pizza", "sushi", "gym"))
        assertEquals(emptySet<String>(), after.ids())
        assertEquals(emptyList<GraphEdge>(), after.edges)
    }

    @Test
    fun `a relabelled fact becomes stated`() {
        val after = graph.relabelled(mapOf("gym" to "Goes to the gym on Mondays"))
        val gym = after.nodes.single { it.id == "gym" }
        assertEquals("Goes to the gym on Mondays", gym.label)
        assertEquals("stated", gym.source)
        assertEquals(graph.nodes.filter { it.id != "gym" }, after.nodes.filter { it.id != "gym" })
    }

    // The topics the map is grouped by: Food & drink holds pizza and sushi, Places holds gym, and
    // sushi is also linked to Places.
    private fun topic(id: String) = GraphNode(id = "cluster:$id", label = id, kind = "cluster")
    private fun inTopic(node: GraphNode, topic: String) = node.copy(clusterId = "cluster:$topic")

    private val topics = KnowledgeGraph(
        nodes = graph.nodes.map {
            when (it.id) {
                "pizza", "sushi" -> inTopic(it, "food")
                "gym" -> inTopic(it, "places")
                else -> it
            }
        } + topic("food") + topic("places"),
        edges = graph.edges + link("sushi", "cluster:places", kind = "topic"),
    )

    @Test
    fun `forgetting a fact keeps the topics the rest of the map is grouped by`() {
        val after = topics.without(setOf("sushi"))
        assertEquals(topics.ids() - "sushi", after.ids())
        assertEquals(listOf("cluster:food", "cluster:places"), after.nodes.filter { it.isCluster }.map { it.id })
        // Grouped by topic still, not fallen back to categories.
        assertEquals(listOf("cluster:food", "cluster:places"), groupsOf(after).map { it.id })
        assertEquals(listOf("pizza"), groupsOf(after).first().factIds)
    }

    @Test
    fun `forgetting a topic's last fact drops the topic`() {
        val after = topics.without(setOf("gym"))
        assertEquals(listOf("cluster:food"), after.nodes.filter { it.isCluster }.map { it.id })
        assertEquals(listOf("cluster:food"), groupsOf(after).map { it.id })
    }

    @Test
    fun `no changes returns the same graph`() {
        assertSame(graph, graph.without(emptySet()))
        assertSame(graph, graph.relabelled(emptyMap()))
    }
}
