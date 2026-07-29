package com.example.nova.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.example.nova.network.NovaApiClient
import kotlinx.coroutines.launch
import java.io.IOException
import java.util.Locale
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin

/**
 * The dial's arc: 270 degrees with a 90 degree gap at the bottom, so the two ends are visually
 * distinct and 0 can't be mistaken for 1. Compose's drawArc puts 0 degrees at 3 o'clock and
 * sweeps clockwise, so 135 degrees starts the track at the lower left.
 */
private const val DIAL_START_ANGLE = 135f
private const val DIAL_SWEEP_ANGLE = 270f

/**
 * DESIGN.md Section 5.7: one dial per Function tool, setting that tool's controller gain
 * between 0 and 1. Gain is how willing a tool is to fire proactively - the backend's
 * dispatcher fires only when `state_confidence * gain >= FIRING_THRESHOLD` - so a dial at 0
 * means "only ever when I ask" and 1 means "act on your own whenever you're confident".
 *
 * V1 gain is entirely user-set (backend/db/schema.sql), which is what makes this screen the
 * seam that sets it. The dial writes the *override*; the learned value underneath is shown
 * but untouched, and Reset clears the override to hand the tool back to reinforcement.
 */
@Composable
fun GainScreen() {
    val scope = rememberCoroutineScope()

    var gains by remember { mutableStateOf<List<NovaApiClient.ToolGain>>(emptyList()) }
    var status by remember { mutableStateOf("Loading tool gains…") }

    // Where the dial sits mid-drag, before the backend has been told. Keyed by tool name and
    // cleared once the PUT comes back, so what's drawn is the server's value except while a
    // finger is down - a request per pixel of drag would be neither kind nor fast.
    var dragging by remember { mutableStateOf<Map<String, Float>>(emptyMap()) }

    suspend fun reload() {
        status = "Loading tool gains…"
        try {
            gains = NovaApiClient.getToolGains()
            status = if (gains.isEmpty()) "No Function tools are registered." else ""
        } catch (e: IOException) {
            status = "Couldn't reach the Nova backend."
        }
    }

    LaunchedEffect(Unit) { reload() }

    fun commit(tool: NovaApiClient.ToolGain, override: Float?) {
        scope.launch {
            try {
                val updated = NovaApiClient.setToolGain(tool.name, override)
                gains = gains.map { if (it.name == updated.name) updated else it }
                status = ""
            } catch (e: IOException) {
                status = "Couldn't save ${tool.name} - backend unreachable."
            } finally {
                // Drop the local value either way: on success the server's is better, and on
                // failure keeping it would show a setting that was never actually stored.
                dragging = dragging - tool.name
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        if (status.isNotBlank()) {
            Text(
                text = status,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(20.dp),
                textAlign = TextAlign.Center
            )
        }

        LazyColumn(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
            contentPadding = PaddingValues(vertical = 20.dp)
        ) {
            items(gains, key = { it.name }) { tool ->
                GainCard(
                    tool = tool,
                    displayValue = dragging[tool.name] ?: tool.effective,
                    onValueChange = { dragging = dragging + (tool.name to it) },
                    onValueCommit = { dragging[tool.name]?.let { commit(tool, it) } },
                    onReset = { commit(tool, null) }
                )
            }
        }
    }
}

@Composable
private fun GainCard(
    tool: NovaApiClient.ToolGain,
    displayValue: Float,
    onValueChange: (Float) -> Unit,
    onValueCommit: () -> Unit,
    onReset: () -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(20.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                text = tool.name,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold
            )
            Spacer(Modifier.height(6.dp))
                        Text(
                text = tool.description,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center
            )

            Spacer(Modifier.height(20.dp))
            GainDial(
                value = displayValue,
                onValueChange = onValueChange,
                onValueCommit = onValueCommit
            )
            Spacer(Modifier.height(12.dp))

            Text(
                text = if (tool.isOverridden) {
                    "Your setting · learned value ${format(tool.value)}"
                } else {
                    "Learned value · not overridden"
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.outline
            )

            if (tool.isOverridden) {
                TextButton(onClick = onReset) { Text("Reset to learned") }
            }
        }
    }
}

/**
 * A rotary dial for a 0..1 value. Drag anywhere on it to sweep, or tap a point on the arc to
 * jump there; [onValueChange] fires continuously while dragging and [onValueCommit] once the
 * finger lifts, so the caller can render live but persist once.
 */
@Composable
private fun GainDial(
    value: Float,
    onValueChange: (Float) -> Unit,
    onValueCommit: () -> Unit,
) {
    val trackColor = MaterialTheme.colorScheme.surfaceVariant
    val progressColor = MaterialTheme.colorScheme.primary

    Box(
        modifier = Modifier.size(150.dp),
        contentAlignment = Alignment.Center
    ) {
        Canvas(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(Unit) {
                    detectTapGestures { offset ->
                        onValueChange(offset.toDialValue(size.width.toFloat(), size.height.toFloat()))
                        onValueCommit()
                    }
                }
                .pointerInput(Unit) {
                    detectDragGestures(onDragEnd = onValueCommit) { change, _ ->
                        change.consume()
                        onValueChange(
                            change.position.toDialValue(size.width.toFloat(), size.height.toFloat())
                        )
                    }
                }
        ) {
            val stroke = 14.dp.toPx()
            val arcTopLeft = Offset(stroke / 2, stroke / 2)
            val arcSize = Size(size.width - stroke, size.height - stroke)
            val clamped = value.coerceIn(0f, 1f)

            drawArc(
                color = trackColor,
                startAngle = DIAL_START_ANGLE,
                sweepAngle = DIAL_SWEEP_ANGLE,
                useCenter = false,
                topLeft = arcTopLeft,
                size = arcSize,
                style = Stroke(width = stroke, cap = StrokeCap.Round)
            )
            drawArc(
                color = progressColor,
                startAngle = DIAL_START_ANGLE,
                sweepAngle = DIAL_SWEEP_ANGLE * clamped,
                useCenter = false,
                topLeft = arcTopLeft,
                size = arcSize,
                style = Stroke(width = stroke, cap = StrokeCap.Round)
            )

            // The knob rides the centre of the track, at the angle the value maps to.
            val knobRadians = Math.toRadians((DIAL_START_ANGLE + DIAL_SWEEP_ANGLE * clamped).toDouble())
            val trackRadius = (size.minDimension - stroke) / 2
            drawCircle(
                color = progressColor,
                radius = stroke * 0.8f,
                center = Offset(
                    x = size.width / 2 + (trackRadius * cos(knobRadians)).toFloat(),
                    y = size.height / 2 + (trackRadius * sin(knobRadians)).toFloat()
                )
            )
        }

        Text(
            text = format(value),
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold
        )
    }
}

/**
 * Where a touch on the dial falls on the 0..1 scale.
 *
 * Screen coordinates run y-down, which makes atan2 increase clockwise from 3 o'clock - the same
 * convention drawArc uses, so the touch angle and the drawn angle agree without correction.
 * A touch in the 90 degree gap at the bottom is off the scale entirely; those snap to whichever
 * end is nearer, so dragging past an end parks there instead of wrapping around to the other.
 */
private fun Offset.toDialValue(width: Float, height: Float): Float {
    val degrees = Math.toDegrees(
        atan2((y - height / 2).toDouble(), (x - width / 2).toDouble())
    ).toFloat()

    var fromStart = (degrees - DIAL_START_ANGLE) % 360f
    if (fromStart < 0) fromStart += 360f

    if (fromStart > DIAL_SWEEP_ANGLE) {
        val intoGap = fromStart - DIAL_SWEEP_ANGLE
        return if (intoGap < (360f - DIAL_SWEEP_ANGLE) / 2) 1f else 0f
    }
    return (fromStart / DIAL_SWEEP_ANGLE).coerceIn(0f, 1f)
}

private fun format(value: Float): String = String.format(Locale.US, "%.2f", value)
