package com.example.novav2.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Nova always renders in its own dark/blue brand theme, regardless of system light/dark setting.
private val NovaColorScheme = darkColorScheme(
    primary = NovaBlue,
    onPrimary = NovaOnBlue,
    primaryContainer = NovaBlueContainer,
    onPrimaryContainer = NovaAccentLight,
    secondary = NovaBlueDim,
    onSecondary = Color.White,
    secondaryContainer = NovaSecondaryContainer,
    onSecondaryContainer = NovaOnBackground,
    tertiary = NovaAccentLight,
    onTertiary = NovaOnBlue,
    background = NovaBackground,
    onBackground = NovaOnBackground,
    surface = NovaSurface,
    onSurface = NovaOnBackground,
    surfaceVariant = NovaSurfaceVariant,
    onSurfaceVariant = NovaOnSurfaceMuted,
    surfaceContainer = NovaSurfaceContainer,
    surfaceContainerHigh = NovaSurfaceContainerHigh,
    surfaceContainerHighest = NovaSurfaceContainerHighest,
    outline = NovaOutline,
    error = NovaError,
    onError = Color.White
)

@Composable
fun NovaTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = NovaColorScheme,
        typography = Typography,
        content = content
    )
}
