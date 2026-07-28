package com.example.nova.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Nova always renders in its own dark/purple brand theme, regardless of system light/dark setting.
private val NovaColorScheme = darkColorScheme(
    primary = NovaPurple,
    onPrimary = NovaOnPurple,
    primaryContainer = NovaPurpleContainer,
    onPrimaryContainer = NovaAccentPink,
    secondary = NovaPurpleDim,
    onSecondary = Color.White,
    tertiary = NovaAccentPink,
    onTertiary = NovaOnPurple,
    background = NovaBackground,
    onBackground = NovaOnBackground,
    surface = NovaSurface,
    onSurface = NovaOnBackground,
    surfaceVariant = NovaSurfaceVariant,
    onSurfaceVariant = NovaOnSurfaceMuted,
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
