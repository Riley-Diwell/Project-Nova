package com.example.novav2.knowledge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class KnowledgeMapLayoutTest {
    private fun group(id: String, vararg facts: String) = MapGroup(id, id, facts.toList())

    private val food = group("food", "pizza", "sushi", "bagels")
    private val travel = group("travel", "bus", "bike")

    @Test
    fun `the same groups always lay out the same way`() {
        assertEquals(layoutOf(listOf(food, travel)), layoutOf(listOf(travel, food)))
    }

    @Test
    fun `a new group never moves the groups already placed`() {
        val first = layoutOf(listOf(food, travel))
        val second = layoutOf(listOf(food, travel, group("health", "peanuts")), first)
        assertEquals(first.groups["food"], second.groups["food"])
        assertEquals(first.groups["travel"], second.groups["travel"])
        assertNotNull(second.groups["health"])
    }

    @Test
    fun `groups never overlap`() {
        var layout: MapLayout? = null
        val groups = mutableListOf<MapGroup>()
        for (i in 0 until 12) {
            groups += group("g$i", *Array(1 + i % 7) { "g$i-f$it" })
            layout = layoutOf(groups, layout)
        }
        val boxes = groups.map { layout!!.groupBox(it.id)!! }
        for (i in boxes.indices) for (j in i + 1 until boxes.size) {
            assertFalse("g$i overlaps g$j", boxes[i].intersects(boxes[j]))
        }
    }

    @Test
    fun `facts keep their cell, and a removed fact leaves a gap rather than shifting the rest`() {
        val first = layoutOf(listOf(food))
        val without = layoutOf(listOf(group("food", "pizza", "bagels")), first)
        assertEquals(first.slots["pizza"], without.slots["pizza"])
        assertEquals(first.slots["bagels"], without.slots["bagels"])

        // A new fact takes the gap.
        val refilled = layoutOf(listOf(group("food", "pizza", "bagels", "ramen")), without)
        assertEquals(first.slots["sushi"]!!.index, refilled.slots["ramen"]!!.index)
    }

    @Test
    fun `a fact that changes group gets a cell in the new one`() {
        val first = layoutOf(listOf(food, travel))
        val moved = layoutOf(listOf(group("food", "pizza", "bagels"), group("travel", "bus", "bike", "sushi")), first)
        assertEquals("travel", moved.slots["sushi"]!!.group)
        assertEquals(2, moved.slots["sushi"]!!.index)
    }

    @Test
    fun `a group that goes frees its place`() {
        val first = layoutOf(listOf(food, travel))
        val after = layoutOf(listOf(food), first)
        assertNull(after.groups["travel"])
        assertNull(after.factPosition("bus"))
    }

    @Test
    fun `facts sit inside their group's box`() {
        val layout = layoutOf(listOf(food, travel))
        for (id in listOf("pizza", "sushi", "bagels")) {
            val box = layout.groupBox("food")!!
            assert(box.contains(layout.factPosition(id)!!)) { "$id outside food" }
        }
    }

    @Test
    fun `a layout survives being saved and read back`() {
        val layout = layoutOf(listOf(food, travel))
        assertEquals(layout, layoutFromJson(layout.toJson()))
    }

    @Test
    fun `columns suit the group size`() {
        assertEquals(1, colsFor(1))
        assertEquals(2, colsFor(2))
        assertEquals(3, colsFor(4))
        assertEquals(MapLayout.MAX_COLS, colsFor(200))
    }
}
