package com.example.novav2.knowledge

import com.example.novav2.network.NovaApiClient.GraphEdge
import com.example.novav2.network.NovaApiClient.GraphNode
import com.example.novav2.network.NovaApiClient.KnowledgeGraph
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The Knowledge Map's saved copy must read back as exactly the graph that was saved. */
class SavedGraphTest {
    private val graph = KnowledgeGraph(
        nodes = listOf(
            GraphNode(id = "cat:opinions", label = "opinions", kind = "category", category = listOf("opinions")),
            GraphNode(
                id = "f1", label = "Likes “bagels”", kind = "fact", source = "derived", confidence = 0.9f,
                category = listOf("opinions"), support = 4, detail = "said so twice", noteId = "n1",
            ),
            GraphNode(id = "f2", label = "Parks on level 3", kind = "fact", source = "stated"),
        ),
        edges = listOf(
            GraphEdge("cat:opinions", "f1", "category", 1f),
            GraphEdge("f1", "f2", "similar", 0.93f),
        ),
    )

    @Test
    fun `round trips a graph with its tag and owner`() {
        val saved = SavedGraph(owner = "user-a", etag = "\"abc123\"", graph = graph)
        assertEquals(saved, SavedGraph.decode(saved.encode()))
    }

    @Test
    fun `round trips without a tag`() {
        val saved = SavedGraph(owner = "user-a", etag = null, graph = graph)
        assertEquals(saved, SavedGraph.decode(saved.encode()))
    }

    @Test
    fun `unreadable is no cache rather than a crash`() {
        assertNull(SavedGraph.decode(""))
        assertNull(SavedGraph.decode("{\"graph\": {}}"))
        assertNull(SavedGraph.decode("not json"))
    }
}
