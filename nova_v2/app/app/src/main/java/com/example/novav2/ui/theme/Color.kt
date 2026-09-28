package com.example.novav2.ui.theme

import androidx.compose.ui.graphics.Color

// Nova brand palette: dark grey base with a blue accent
val NovaBackground = Color(0xFF121214)
val NovaSurface = Color(0xFF1C1C21)
val NovaSurfaceVariant = Color(0xFF26262E)

// Borders (text fields, outlined buttons) - needs to stand clear of the dialog container
// (surfaceContainerHigh) and cards, or an unlabelled field vanishes entirely: the New note
// dialog used to look empty because its field's border was the dialog's own colour.
val NovaOutline = Color(0xFF5E5E6B)
// Dividers and hairlines - meant to recede, unlike the outline above.
val NovaOutlineVariant = Color(0xFF34343D)

val NovaBlue = Color(0xFF487EE4)
val NovaOnBlue = Color(0xFF0D1B33)
val NovaBlueDim = Color(0xFF5E76A8)
val NovaBlueContainer = Color(0xFF223A63)
val NovaAccentLight = Color(0xFFA7C4F2)

val NovaOnBackground = Color(0xFFECECF1)
val NovaOnSurfaceMuted = Color(0xFFA6A6B0)
val NovaError = Color(0xFFFF6B6B)
val NovaErrorContainer = Color(0xFF4A1F22)

// Signal-status semantics (StateScreen's chips) - separate from the blue brand accent above,
// since "this needs attention" and "this is Nova's own colour" are different questions.
// NovaError above doubles as the "critical" tone rather than adding a redundant fourth red.
val NovaOk = Color(0xFF6FCF8E)
val NovaWarn = Color(0xFFE0B44C)

// Knowledge Map: a belief NOVA worked out for itself, set apart from the brand blue used for
// things the user said directly.
val NovaDerived = Color(0xFF4FC3B0)

// M3 tokens the base darkColorScheme() otherwise fills in from its own baseline
// palette (which leans purple) - set explicitly so nothing purple leaks through
// on components we don't style directly (nav bar, cards, menus, snackbars, etc).
val NovaSecondaryContainer = NovaSurfaceVariant
val NovaSurfaceContainerLowest = Color(0xFF0E0E10)
val NovaSurfaceContainerLow = Color(0xFF18181C)
val NovaSurfaceContainer = NovaSurfaceVariant
val NovaSurfaceContainerHigh = Color(0xFF2E2E36)
val NovaSurfaceContainerHighest = Color(0xFF3A3A44)
val NovaInverseSurface = Color(0xFFE4E4EA)
val NovaInverseOnSurface = Color(0xFF1C1C21)
val NovaInversePrimary = Color(0xFF2D5DB8)
