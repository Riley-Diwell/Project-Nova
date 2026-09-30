package com.example.novav2.knowledge

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class KnowledgeMapLayoutTest {
    private fun group(id: String, vararg facts: String) = MapGroup(id, id, facts.toList())
    private fun similar(a: String, b: String, w: Float) = Link(a, b, w, Link.Kind.SIMILAR)
    private fun alsoAbout(fact: String, topic: String, w: Float) = Link(fact, topic, w, Link.Kind.TOPIC)

    private val food = group("food", "pizza", "sushi", "bagels")
    private val travel = group("travel", "bus", "bike")

    private fun MapLayout.gap(a: String, b: String) =
        (factPosition(a) ?: hubPosition(a)!!).distanceTo(factPosition(b) ?: hubPosition(b)!!)

    @Test
    fun `the same inputs always lay out the same way`() {
        val links = listOf(similar("pizza", "sushi", 0.7f))
        assertEquals(layoutOf(listOf(food, travel), links), layoutOf(listOf(food, travel), links))
    }

    @Test
    fun `more similar facts sit closer together`() {
        val layout = layoutOf(listOf(food), listOf(similar("pizza", "sushi", 0.74f), similar("pizza", "bagels", 0.56f)))
        assert(layout.gap("pizza", "sushi") < layout.gap("pizza", "bagels")) {
            "close pair ${layout.gap("pizza", "sushi")} vs loose pair ${layout.gap("pizza", "bagels")}"
        }
    }

    @Test
    fun `a fact also about another topic is drawn towards it`() {
        val groups = listOf(group("food", "pizza", "melon"), group("sleep", "bedtime"))
        val alone = layoutOf(groups)
        val linked = layoutOf(groups, listOf(alsoAbout("melon", "sleep", 0.68f)))
        assert(linked.gap("melon", "sleep") < alone.gap("melon", "sleep")) {
            "with the link ${linked.gap("melon", "sleep")}, without ${alone.gap("melon", "sleep")}"
        }
    }

    @Test
    fun `a new fact settles in without moving what was already there`() {
        val links = listOf(similar("pizza", "sushi", 0.7f), similar("bus", "bike", 0.68f))
        val first = layoutOf(listOf(food, travel), links)
        val second = layoutOf(
            listOf(group("food", "pizza", "sushi", "bagels", "ramen"), travel),
            links + similar("ramen", "sushi", 0.72f), first,
        )
        for (id in listOf("pizza", "sushi", "bagels", "bus", "bike")) {
            val moved = first.factPosition(id)!!.distanceTo(second.factPosition(id)!!)
            assert(moved < 25f) { "$id moved $moved" }
        }
        for (hub in listOf("food", "travel")) {
            assert(first.hubPosition(hub)!!.distanceTo(second.hubPosition(hub)!!) < 25f) { "$hub moved" }
        }
        assertNotNull(second.factPosition("ramen"))
    }

    @Test
    fun `nothing lands on top of anything else`() {
        val groups = (0 until 6).map { g -> group("g$g", *Array(2 + (g * 5) % 11) { "g$g-f$it" }) }
        val links = groups.flatMap { g -> g.factIds.zipWithNext { a, b -> similar(a, b, 0.7f) } }
        val layout = layoutOf(groups, links)
        val facts = groups.flatMap { it.factIds }
        for (i in facts.indices) for (j in i + 1 until facts.size) {
            assert(layout.gap(facts[i], facts[j]) > 24f) { "${facts[i]} and ${facts[j]} overlap" }
        }
        for (i in groups.indices) for (j in i + 1 until groups.size) {
            assert(layout.gap(groups[i].id, groups[j].id) > 120f) { "${groups[i].id} and ${groups[j].id} overlap" }
        }
    }

    @Test
    fun `facts sit inside their topic's box`() {
        val layout = layoutOf(listOf(food, travel))
        for (id in food.factIds) {
            assert(layout.groupBox("food")!!.contains(layout.factPosition(id)!!)) { "$id outside food" }
        }
    }

    @Test
    fun `a topic that goes takes its facts with it`() {
        val first = layoutOf(listOf(food, travel))
        val after = layoutOf(listOf(food), previous = first)
        assertNull(after.hubPosition("travel"))
        assertNull(after.factPosition("bus"))
    }

    @Test
    fun `a layout survives being saved and read back`() {
        val layout = layoutOf(listOf(food, travel), listOf(similar("pizza", "sushi", 0.7f)))
        assertEquals(layout, layoutFromJson(layout.toJson()))
    }

    @Test
    fun `a layout saved by an older version is replaced rather than misread`() {
        val grid = JSONObject("""{"groups":[{"id":"food","x":0,"y":0,"cols":2,"reserved":3}],"slots":[]}""")
        assertNull(layoutFromJson(grid))
    }
}
