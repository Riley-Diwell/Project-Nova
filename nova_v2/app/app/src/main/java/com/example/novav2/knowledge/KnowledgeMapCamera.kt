package com.example.novav2.knowledge

import kotlin.math.min

/**
 * The map's view: how far in ([zoom]) and where ([panX], [panY], in pixels).
 *
 * A world point w (dp, from [MapLayout]) is drawn at  viewportCentre + pan + w * zoom * density,
 * so zoom 1 shows the layout at its natural size on any screen. Pure maths, so the gesture and
 * fly-to behaviour can be unit tested.
 */
data class Camera(val zoom: Float = 1f, val panX: Float = 0f, val panY: Float = 0f)

/**
 * How much detail the map draws, by zoom:
 *  - [GROUPS]: zoomed out - each group is a block with its heading and a count.
 *  - [DOTS]: facts as dots with a one-line label, the heading above each group.
 *  - [CARDS]: close up - each fact as a small card: what it says and where it came from.
 */
enum class Lod { GROUPS, DOTS, CARDS }

object CameraMath {
    const val MIN_ZOOM = 0.15f
    const val MAX_ZOOM = 3f
    const val DOTS_AT = 0.45f
    const val CARDS_AT = 0.9f
    /** Crossing a threshold takes this much more zoom than crossing back, so it doesn't flicker. */
    const val HYSTERESIS = 0.05f

    /** A world point's position relative to the viewport centre, in pixels. */
    fun toScreen(camera: Camera, world: Vec, density: Float): Vec =
        Vec(camera.panX + world.x * camera.zoom * density, camera.panY + world.y * camera.zoom * density)

    /** The world point under a screen position (relative to the viewport centre). */
    fun toWorld(camera: Camera, screen: Vec, density: Float): Vec =
        Vec((screen.x - camera.panX) / (camera.zoom * density), (screen.y - camera.panY) / (camera.zoom * density))

    /** Zoom by [factor] keeping the world point under [focus] (the pinch centre) where it is. */
    fun zoomAbout(camera: Camera, focus: Vec, factor: Float, density: Float): Camera {
        val anchor = toWorld(camera, focus, density)
        val zoom = (camera.zoom * factor).coerceIn(MIN_ZOOM, MAX_ZOOM)
        return Camera(zoom, focus.x - anchor.x * zoom * density, focus.y - anchor.y * zoom * density)
    }

    fun panBy(camera: Camera, dx: Float, dy: Float) = camera.copy(panX = camera.panX + dx, panY = camera.panY + dy)

    /** The camera that shows all of [box] in a [width] x [height] pixel viewport, centred. */
    fun fit(box: Box, width: Float, height: Float, density: Float, paddingPx: Float, maxZoom: Float = MAX_ZOOM): Camera {
        val usableW = (width - 2 * paddingPx).coerceAtLeast(1f)
        val usableH = (height - 2 * paddingPx).coerceAtLeast(1f)
        val zoom = min(usableW / (box.width * density), usableH / (box.height * density))
            .coerceIn(MIN_ZOOM, min(maxZoom, MAX_ZOOM))
        val c = box.centre
        return Camera(zoom, -c.x * zoom * density, -c.y * zoom * density)
    }

    /** The same view with [zoom], still centred on whatever [camera] was centred on. */
    fun withZoom(camera: Camera, zoom: Float): Camera =
        zoomAbout(camera, Vec(0f, 0f), zoom / camera.zoom, 1f)

    fun lodFor(zoom: Float, previous: Lod? = null): Lod {
        // Already above a threshold: stay until well below it. Below it: go only once well above.
        // With no previous level, the threshold itself.
        fun above(threshold: Float, wasAbove: Boolean?) = when (wasAbove) {
            null -> zoom >= threshold
            true -> zoom >= threshold - HYSTERESIS
            false -> zoom >= threshold + HYSTERESIS
        }
        val cards = above(CARDS_AT, previous?.let { it == Lod.CARDS })
        val dots = above(DOTS_AT, previous?.let { it != Lod.GROUPS })
        return when {
            cards -> Lod.CARDS
            dots -> Lod.DOTS
            else -> Lod.GROUPS
        }
    }

    /** Straight-line interpolation between two views, for animating a fly-to. */
    fun lerp(a: Camera, b: Camera, t: Float) = Camera(
        a.zoom + (b.zoom - a.zoom) * t,
        a.panX + (b.panX - a.panX) * t,
        a.panY + (b.panY - a.panY) * t,
    )
}
