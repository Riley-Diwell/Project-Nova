package com.example.novav2.ui.theme

import androidx.compose.ui.graphics.Color

// Nova brand palette: dark grey base with a blue accent
val NovaBackground = Color(0xFF121214)
val NovaSurface = Color(0xFF1C1C21)
val NovaSurfaceVariant = Color(0xFF26262E)
val NovaOutline = Color(0xFF3A3A44)

val NovaBlue = Color(0xFF487EE4)
val NovaOnBlue = Color(0xFF0D1B33)
val NovaBlueDim = Color(0xFF5E76A8)
val NovaBlueContainer = Color(0xFF223A63)
val NovaAccentLight = Color(0xFFA7C4F2)

val NovaOnBackground = Color(0xFFECECF1)
val NovaOnSurfaceMuted = Color(0xFFA6A6B0)
val NovaError = Color(0xFFFF6B6B)

// Signal-status semantics (StateScreen's chips) - separate from the blue brand accent above,
// since "this needs attention" and "this is Nova's own colour" are different questions.
// NovaError above doubles as the "critical" tone rather than adding a redundant fourth red.
val NovaOk = Color(0xFF6FCF8E)
val NovaWarn = Color(0xFFE0B44C)

// M3 tokens the base darkColorScheme() otherwise fills in from its own baseline
// palette (which leans purple) - set explicitly so nothing purple leaks through
// on components we don't style directly (nav bar, cards, menus, etc).
val NovaSecondaryContainer = NovaSurfaceVariant
val NovaSurfaceContainer = NovaSurfaceVariant
val NovaSurfaceContainerHigh = Color(0xFF2E2E36)
val NovaSurfaceContainerHighest = NovaOutline
