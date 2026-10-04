package com.example.novav2.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.ColorFilter
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.GlanceTheme
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalContext
import androidx.glance.LocalSize
import androidx.glance.action.Action
import androidx.glance.action.actionParametersOf
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.CheckBox
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.appWidgetBackground
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.material3.ColorProviders
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import com.example.novav2.R
import com.example.novav2.ui.theme.NovaColorScheme

/**
 * The home-screen widget: "what's next for me?" at a glance, built from [WidgetSnapshot].
 *
 * Calm on purpose (the Attention pillar): no badges, no counts styled as alarms, no animation -
 * small type in the app's own colours. Nova always renders in its dark brand theme, so the widget
 * does too rather than following dynamic colour. Three sizes:
 * - small: the single most relevant line ([WidgetSnapshot.next]);
 * - medium: a "Next" header with the date, the Now/Next event row and up to 2 reminders;
 * - large: up to 3 reminders, the held count, the device's status and a mic button.
 */
class NovaWidget : GlanceAppWidget() {
    override val sizeMode = SizeMode.Responsive(setOf(SMALL, MEDIUM, LARGE))

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val first = WidgetUpdater.refreshes.value
        val initial = WidgetSnapshotLoader.load(context)
        WidgetUpdater.scheduleNextBoundary(context, initial.nextBoundaryMillis)
        provideContent {
            // The session stays up after this first load, and WidgetUpdater's redraws only
            // recompose it: reload on each one, or a ticked reminder or a sign-out never shows.
            val refresh by WidgetUpdater.refreshes.collectAsState()
            val snapshot by produceState(initial, refresh) {
                if (refresh == first) return@produceState
                value = WidgetSnapshotLoader.load(context)
                WidgetUpdater.scheduleNextBoundary(context, value.nextBoundaryMillis)
            }
            GlanceTheme(colors = colors) { Content(snapshot) }
        }
    }

    companion object {
        val SMALL = DpSize(110.dp, 40.dp)
        val MEDIUM = DpSize(250.dp, 110.dp)
        val LARGE = DpSize(250.dp, 180.dp)

        private val colors = ColorProviders(scheme = NovaColorScheme)
    }
}

class NovaWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = NovaWidget()

    /** The last widget was removed - nothing left to keep fresh. */
    override fun onDisabled(context: Context) {
        super.onDisabled(context)
        WidgetUpdater.scheduleNextBoundary(context, null)
    }
}

@Composable
private fun Content(snapshot: WidgetSnapshot) {
    val context = LocalContext.current
    val size = LocalSize.current
    val root = GlanceModifier.fillMaxSize().appWidgetBackground().cornerRadius(16.dp)
        .background(GlanceTheme.colors.surface)

    if (!snapshot.signedIn) {
        Column(
            modifier = root.padding(12.dp).clickable(actionStartActivity(WidgetIntents.openApp(context))),
            verticalAlignment = Alignment.CenterVertically,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(snapshot.next.title, style = titleStyle())
        }
        return
    }

    when {
        size.width >= NovaWidget.LARGE.width && size.height >= NovaWidget.LARGE.height ->
            Column(modifier = root.padding(12.dp)) {
                Header(snapshot)
                Body(snapshot, WidgetSnapshot.LARGE_REMINDERS)
                Spacer(GlanceModifier.defaultWeight())
                Footer(snapshot)
            }
        size.width >= NovaWidget.MEDIUM.width && size.height >= NovaWidget.MEDIUM.height ->
            Column(modifier = root.padding(12.dp)) {
                Header(snapshot)
                Body(snapshot, WidgetSnapshot.MEDIUM_REMINDERS)
            }
        else ->
            Column(
                modifier = root.padding(horizontal = 12.dp, vertical = 6.dp)
                    .clickable(actionFor(context, snapshot.next)),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(snapshot.next.title, style = titleStyle(), maxLines = 1)
                snapshot.next.detail?.let { Text(it, style = mutedStyle(), maxLines = 1) }
            }
    }
}

@Composable
private fun Header(snapshot: WidgetSnapshot) {
    Row(modifier = GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(
            "Next",
            style = TextStyle(color = GlanceTheme.colors.primary, fontSize = 13.sp, fontWeight = FontWeight.Medium),
        )
        Spacer(GlanceModifier.defaultWeight())
        Text(snapshot.dateLabel, style = mutedStyle())
    }
    Spacer(GlanceModifier.height(4.dp))
}

@Composable
private fun Body(snapshot: WidgetSnapshot, maxReminders: Int) {
    val context = LocalContext.current
    snapshot.nowNext?.let { line ->
        Column(modifier = GlanceModifier.fillMaxWidth().padding(vertical = 2.dp).clickable(actionFor(context, line))) {
            Text(line.title, style = titleStyle(), maxLines = 1)
            line.detail?.let { Text(it, style = mutedStyle(), maxLines = 1) }
        }
    }
    snapshot.reminders.take(maxReminders).forEach { ReminderRow(it) }
    if (snapshot.nowNext == null && snapshot.reminders.isEmpty()) {
        Text("Nothing coming up", style = mutedStyle())
    }
}

@Composable
private fun ReminderRow(row: WidgetSnapshot.ReminderRow) {
    val context = LocalContext.current
    Row(modifier = GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        CheckBox(
            checked = false,
            onCheckedChange = actionRunCallback<CompleteReminderAction>(
                actionParametersOf(CompleteReminderAction.REMINDER_ID to row.id)
            ),
        )
        Text(
            row.text,
            style = bodyStyle(),
            maxLines = 1,
            modifier = GlanceModifier.defaultWeight()
                .clickable(actionStartActivity(WidgetIntents.openReminders(context))),
        )
        Spacer(GlanceModifier.width(8.dp))
        Text(row.whenLabel, style = mutedStyle(), maxLines = 1)
    }
}

@Composable
private fun Footer(snapshot: WidgetSnapshot) {
    val context = LocalContext.current
    Row(modifier = GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(
            modifier = GlanceModifier.defaultWeight()
                .clickable(actionStartActivity(WidgetIntents.openReminders(context))),
        ) {
            if (snapshot.heldCount > 0) {
                Text("Held until you're free: ${snapshot.heldCount}", style = mutedStyle(), maxLines = 1)
            }
            snapshot.device?.let { Text(it, style = mutedStyle(), maxLines = 1) }
        }
        Image(
            provider = ImageProvider(R.drawable.ic_widget_mic),
            contentDescription = "Talk to Nova",
            colorFilter = ColorFilter.tint(GlanceTheme.colors.primary),
            modifier = GlanceModifier.size(36.dp).padding(6.dp)
                .clickable(actionStartActivity(WidgetIntents.talk(context))),
        )
    }
}

private fun actionFor(context: Context, line: WidgetSnapshot.Line): Action = actionStartActivity(
    when (line.target) {
        WidgetSnapshot.Target.REMINDERS -> WidgetIntents.openReminders(context)
        WidgetSnapshot.Target.EVENT -> line.eventId?.let { WidgetIntents.openEvent(context, it) }
            ?: WidgetIntents.openApp(context)
        WidgetSnapshot.Target.APP -> WidgetIntents.openApp(context)
    }
)

@Composable
private fun titleStyle() = TextStyle(color = GlanceTheme.colors.onSurface, fontSize = 14.sp, fontWeight = FontWeight.Medium)

@Composable
private fun bodyStyle() = TextStyle(color = GlanceTheme.colors.onSurface, fontSize = 13.sp)

@Composable
private fun mutedStyle() = TextStyle(color = GlanceTheme.colors.onSurfaceVariant, fontSize = 12.sp)
