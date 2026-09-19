package com.example.novav2.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
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
import com.example.novav2.state.TravelModePreference

/** "driving" / "transit" / "walking" - navigation_departure_time's mode enum, in display order. */
private val TRAVEL_MODES = listOf("transit", "walking", "driving")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen() {
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
    }
}
