package com.example.novav2.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.SwitchColors
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

// Nova always renders in its own dark/blue brand theme, regardless of system light/dark setting.
private val NovaColorScheme = darkColorScheme(
    primary = NovaBlue,
    onPrimary = Color.White,
    primaryContainer = NovaBlueContainer,
    onPrimaryContainer = NovaAccentLight,
    inversePrimary = NovaInversePrimary,
    secondary = NovaBlueDim,
    onSecondary = Color.White,
    secondaryContainer = NovaSecondaryContainer,
    onSecondaryContainer = NovaOnBackground,
    tertiary = NovaAccentLight,
    onTertiary = NovaOnBlue,
    tertiaryContainer = NovaBlueContainer,
    onTertiaryContainer = NovaAccentLight,
    background = NovaBackground,
    onBackground = NovaOnBackground,
    surface = NovaSurface,
    onSurface = NovaOnBackground,
    surfaceVariant = NovaSurfaceVariant,
    onSurfaceVariant = NovaOnSurfaceMuted,
    surfaceTint = NovaBlue,
    inverseSurface = NovaInverseSurface,
    inverseOnSurface = NovaInverseOnSurface,
    surfaceBright = NovaSurfaceContainerHighest,
    surfaceDim = NovaBackground,
    surfaceContainerLowest = NovaSurfaceContainerLowest,
    surfaceContainerLow = NovaSurfaceContainerLow,
    surfaceContainer = NovaSurfaceContainer,
    surfaceContainerHigh = NovaSurfaceContainerHigh,
    surfaceContainerHighest = NovaSurfaceContainerHighest,
    outline = NovaOutline,
    outlineVariant = NovaOutlineVariant,
    error = NovaError,
    onError = Color.White,
    errorContainer = NovaErrorContainer,
    onErrorContainer = NovaError,
    scrim = Color.Black,
)

// Slightly softer corners than M3's defaults - cards and dialogs read as one family.
private val NovaShapes = Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(20.dp),
    extraLarge = RoundedCornerShape(28.dp),
)

@Composable
fun NovaTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = NovaColorScheme,
        typography = Typography,
        shapes = NovaShapes,
        content = content
    )
}

// M3's unchecked Switch uses surfaceContainerHighest for the track and outline for the
// thumb/border - an "off" switch could vanish into the background. Lift the thumb and border
// to the muted text tone so the off state stays readable.
@Composable
fun novaSwitchColors(): SwitchColors = SwitchDefaults.colors(
    uncheckedThumbColor = NovaOnSurfaceMuted,
    uncheckedTrackColor = NovaSurfaceVariant,
    uncheckedBorderColor = NovaOnSurfaceMuted
)
