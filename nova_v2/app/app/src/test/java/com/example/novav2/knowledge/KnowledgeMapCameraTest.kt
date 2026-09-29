package com.example.novav2.knowledge

import org.junit.Assert.assertEquals
import org.junit.Test

class KnowledgeMapCameraTest {
    private val density = 2.5f

    @Test
    fun `pinching keeps the point under the fingers still`() {
        val camera = Camera(zoom = 0.8f, panX = 40f, panY = -25f)
        val fingers = Vec(120f, 60f)
        val before = CameraMath.toWorld(camera, fingers, density)
        val after = CameraMath.toWorld(CameraMath.zoomAbout(camera, fingers, 1.7f, density), fingers, density)
        assertEquals(before.x, after.x, 0.01f)
        assertEquals(before.y, after.y, 0.01f)
    }

    @Test
    fun `zoom is clamped at both ends`() {
        assertEquals(CameraMath.MAX_ZOOM, CameraMath.zoomAbout(Camera(2f), Vec(0f, 0f), 10f, density).zoom)
        assertEquals(CameraMath.MIN_ZOOM, CameraMath.zoomAbout(Camera(0.2f), Vec(0f, 0f), 0.01f, density).zoom)
    }

    @Test
    fun `fit centres the box and shows all of it`() {
        val box = Box(100f, 200f, 500f, 400f)
        val camera = CameraMath.fit(box, width = 1080f, height = 1600f, density = density, paddingPx = 60f)
        val centre = CameraMath.toScreen(camera, box.centre, density)
        assertEquals(0f, centre.x, 0.01f)
        assertEquals(0f, centre.y, 0.01f)
        val topLeft = CameraMath.toScreen(camera, Vec(box.left, box.top), density)
        assert(topLeft.x >= -540f + 59f) { "left edge off screen: $topLeft" }
    }

    @Test
    fun `detail steps up with zoom and doesn't flicker at the thresholds`() {
        assertEquals(Lod.OVERVIEW, CameraMath.lodFor(0.3f))
        assertEquals(Lod.LABELS, CameraMath.lodFor(0.9f))
        assertEquals(Lod.DETAIL, CameraMath.lodFor(2f))

        // Just under the detail threshold: stays on detail if already there, on labels if not.
        val justUnder = CameraMath.DETAIL_AT - 0.02f
        assertEquals(Lod.DETAIL, CameraMath.lodFor(justUnder, Lod.DETAIL))
        assertEquals(Lod.LABELS, CameraMath.lodFor(CameraMath.DETAIL_AT + 0.02f, Lod.LABELS))
    }

    @Test
    fun `withZoom keeps the same centre`() {
        val camera = CameraMath.fit(Box(0f, 0f, 1000f, 1000f), 1000f, 1000f, density, 0f)
        val zoomed = CameraMath.withZoom(camera, 1.5f)
        val centre = CameraMath.toWorld(zoomed, Vec(0f, 0f), density)
        assertEquals(500f, centre.x, 0.01f)
        assertEquals(500f, centre.y, 0.01f)
    }
}
