package com.example.novav2.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.example.novav2.navigation.NovaDestination
import com.example.novav2.profile.ProfileRepository
import com.example.novav2.state.ReminderPreferences
import com.example.novav2.state.TravelModePreference
import com.example.novav2.ui.components.ScreenGutter
import com.example.novav2.ui.components.ScreenHeader
import com.example.novav2.ui.components.SectionLabel
import com.example.novav2.ui.screens.auth.AccountSection
import com.example.novav2.ui.theme.novaSwitchColors

/** "driving" / "transit" / "walking" - navigation_departure_time's mode enum, in display order. */
private val TRAVEL_MODES = listOf("transit", "walking", "driving")

/** Advanced/infrequent screens reached from here instead of their own bottom nav tab - device
 * pairing, per-tool gain tuning, and raw signal state aren't everyday destinations the way
 * Voice/Map/Audit are. Routes themselves are unchanged, still registered in NovaApp's NavHost. */
private val SETTINGS_SUBSCREENS = listOf(
    NovaDestination.Profile to "Your week, sleep and how Nova should behave",
    NovaDestination.Device to "Pair and manage your Nova companion device",
    NovaDestination.Gain to "Tune how proactively each tool fires on its own",
    NovaDestination.State to "Raw signal snapshot Nova fuses into its state read",
)

/** Routes pushed on top of the Settings tab - NovaApp keeps the Settings tab lit while on them. */
val SETTINGS_SUBSCREEN_ROUTES: Set<String> = SETTINGS_SUBSCREENS.map { it.first.route }.toSet()

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(navController: NavController) {
    val context = LocalContext.current
    var travelMode by remember { mutableStateOf(TravelModePreference.get(context) ?: "transit") }
    var snoozeMinutes by remember { mutableStateOf(ReminderPreferences.snoozeMinutes(context)) }
    var speakReminders by remember { mutableStateOf(ReminderPreferences.speakWithHeadphones(context)) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
    ) {
        ScreenHeader(title = "Settings")

        Column(
            modifier = Modifier.padding(start = ScreenGutter, end = ScreenGutter, top = 8.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SectionLabel("Account", Modifier.padding(start = 4.dp))
            SettingsCard { AccountSection() }

            SectionLabel("Preferences", Modifier.padding(start = 4.dp, top = 16.dp))
            SettingsCard {
                SettingTitle(
                    "How do you usually get to uni?",
                    "Nova uses this when you ask when to leave, if you haven't said how you're travelling.",
                )
                Spacer(Modifier.height(12.dp))
                SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                    TRAVEL_MODES.forEachIndexed { index, mode ->
                        SegmentedButton(
                            selected = travelMode == mode,
                            onClick = {
                                travelMode = mode
                                TravelModePreference.set(context, mode)
                                // The account's copy too, so it survives a reinstall.
                                ProfileRepository.updateTravelMode(mode)
                            },
                            shape = SegmentedButtonDefaults.itemShape(index, TRAVEL_MODES.size),
                            label = { Text(mode.replaceFirstChar { it.uppercase() }, maxLines = 1) },
                        )
                    }
                }

                SettingDivider()

                SettingTitle(
                    "Snooze reminders for",
                    "Used by the notification's Snooze and when you say “snooze it”.",
                )
                Spacer(Modifier.height(12.dp))
                val choices = ReminderPreferences.SNOOZE_CHOICES
                SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                    choices.forEachIndexed { index, minutes ->
                        SegmentedButton(
                            selected = snoozeMinutes == minutes,
                            onClick = {
                                snoozeMinutes = minutes
                                ReminderPreferences.setSnoozeMinutes(context, minutes)
                            },
                            shape = SegmentedButtonDefaults.itemShape(index, choices.size),
                            // Four segments leave no room for the selected tick beside "15 min" -
                            // with it the label wrapped onto two lines.
                            icon = {},
                            label = { Text("$minutes min", maxLines = 1, overflow = TextOverflow.Clip) },
                        )
                    }
                }

                SettingDivider()

                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        SettingTitle(
                            "Read reminders aloud in headphones",
                            "Only when headphones are connected - never through the phone speaker.",
                        )
                    }
                    Spacer(Modifier.width(16.dp))
                    Switch(
                        checked = speakReminders,
                        onCheckedChange = {
                            speakReminders = it
                            ReminderPreferences.setSpeakWithHeadphones(context, it)
                        },
                        colors = novaSwitchColors(),
                    )
                }
            }

        Column {
            SETTINGS_SUBSCREENS.forEach { (destination, description) ->
                ListItem(
                    headlineContent = { Text(destination.label) },
                    supportingContent = { Text(description) },
                    leadingContent = { Icon(destination.icon, contentDescription = null) },
                    trailingContent = {
                        Icon(Icons.Default.ChevronRight, contentDescription = null)
                    },
                    modifier = Modifier.clickable { navController.navigate(destination.route) }
                )
            }
        }
    }
}
