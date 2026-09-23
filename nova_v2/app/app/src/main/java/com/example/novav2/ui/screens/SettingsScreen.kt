package com.example.novav2.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.example.novav2.navigation.NovaDestination
import com.example.novav2.state.TravelModePreference

/** "driving" / "transit" / "walking" - navigation_departure_time's mode enum, in display order. */
private val TRAVEL_MODES = listOf("transit", "walking", "driving")

/** Advanced/infrequent screens reached from here instead of their own bottom nav tab - device
 * pairing, per-tool gain tuning, and raw signal state aren't everyday destinations the way
 * Voice/Map/Audit are. Routes themselves are unchanged, still registered in NovaApp's NavHost. */
private val SETTINGS_SUBSCREENS = listOf(
    NovaDestination.Device to "Pair and manage your Nova companion device",
    NovaDestination.Gain to "Tune how proactively each tool fires on its own",
    NovaDestination.State to "Raw signal snapshot Nova fuses into its state read",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(navController: NavController) {
    val context = LocalContext.current
    var travelMode by remember { mutableStateOf(TravelModePreference.get(context) ?: "transit") }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp)
    ) {
        Column {
            Text(
                text = "How do you usually get to uni?",
                style = MaterialTheme.typography.bodyLarge
            )
            Text(
                text = "Nova uses this when you ask when to leave, if you haven't said " +
                    "how you're travelling.",
                style = MaterialTheme.typography.bodySmall
            )
            SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                TRAVEL_MODES.forEachIndexed { index, mode ->
                    SegmentedButton(
                        selected = travelMode == mode,
                        onClick = {
                            travelMode = mode
                            TravelModePreference.set(context, mode)
                        },
                        shape = SegmentedButtonDefaults.itemShape(index, TRAVEL_MODES.size),
                        label = { Text(mode.replaceFirstChar { it.uppercase() }) }
                    )
                }
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
