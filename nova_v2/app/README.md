# NOVA V2 — Android app

Fresh Android project (Gradle root here, `:app` module under `app/`), scaffolded
from `nova_v1/android`'s config but not its code. `nova_v1/android` stays as-is
and deployable — this is a deliberate module-by-module port, not a copy of the
whole tree, so V2 doesn't inherit V1's cleanup debt.

- **applicationId / namespace:** `com.example.novav2` (was `com.example.nova`)
- **Root project name:** `NovaV2`
- Gradle wrapper, AGP/Kotlin/library versions (`gradle/libs.versions.toml`),
  and `app/build.gradle.kts` dependencies are copied from V1 unchanged — no
  version-bump decisions made here.

**Status: the module-by-module port below is done.** Every file under V1's
`app/src/main/java/com/example/nova/` has been read and ported except the two
Android-Studio-template test stubs (`ExampleUnitTest.kt`,
`ExampleInstrumentedTest.kt` — boilerplate, not real coverage). No dead code
found anywhere in the Android app, unlike the backend — see
`nova_v2/server/README.md` for the (several) dead files found there.

`MainActivity.kt` now does what V1's did: requests `POST_NOTIFICATIONS`,
starts `SignalMonitorService`, and renders `NovaApp()` (the real nav graph,
not a placeholder). Manifest carries every permission `state/`/`service/`
actually use, plus the `ActivityTransitionReceiver` receiver and
`SignalMonitorService` service declarations.

## What was ported, in the order it happened

1. **`model/`, `network/`** — `UserState.kt`, `CalendarEventInfo.kt`,
   `UserProfile.kt`, `ChatMessage.kt`, `NovaApiClient.kt`. Thin data contracts,
   all clean.
   **One deliberate change from V1, not a straight port**: V1's
   `NovaApiClient.BASE_URL` is hardcoded to V1's *live production* Cloud Run
   URL (`https://nova-backend-....run.app`) — not `10.0.2.2:8000` as an
   earlier note here assumed; that was stale. V2 initially pointed at local
   dev instead (`10.0.2.2:8000`) while `nova_v2/server` had no deployment of
   its own. **Updated 2026-09-14**, now that it does: `BASE_URL` points at
   the deployed `nova-v2` Cloud Run service
   (`https://nova-v2-1021689546881.australia-southeast1.run.app`), and
   `local.properties`' `NOVA_API_KEY` (gitignored, machine-local, not
   committed) carries the matching secret so requests actually authenticate.
   Swap `BASE_URL` back to `10.0.2.2:8000` (emulator) or a LAN IP (physical
   device) for local dev against `uvicorn --reload` instead.
2. **`state/`** (all ~19 signal files) + `SignalRepository.kt` +
   `service/SignalMonitorService.kt` — read every one, all clean, all
   self-contained (permission checks, Android API reads). `CalendarSignal.kt`
   and `CalendarWriter.kt` (the two biggest) included. Manifest permissions
   added back all at once rather than incrementally, since none turned out
   to need holding back.
3. **`data/`** (Room DB: `NovaDatabase.kt`, `ChatMessageDao.kt`,
   `ChatMessageEntity.kt`) + `viewmodel/ChatViewModel.kt` — clean, small.
4. **`ui/screens/` + `ui/NovaApp.kt` + `navigation/`** — all 8 screens read
   in full (`VoiceScreen.kt` is the big one at ~750 lines: full voice/text
   pipeline, calendar-action execution, delete-confirmation dialog, TTS
   barge-in outcome reporting — no dead code in it either). Ported verbatim
   including the current hidden-from-nav state: **`NovaDestinations.kt` still
   has Dashboard/Device/Settings commented out of `bottomNavDestinations`**,
   and **`NovaApp.kt`'s onboarding gate is still hardcoded `onboardingComplete
   = true`** with V1's original `TODO: re-enable onboarding gate` comment
   intact. Both are inherited V1 product-state decisions, not bugs — not
   mine to silently resolve while porting.

## Status

- **Builds and runs** — confirmed in Android Studio on an emulator
  (2026-09-14), including a real round trip to the backend (mock-LLM echo
  came back and was spoken via TTS, before the switch to the deployed URL
  above).
- Now pointed at the deployed `nova-v2` service rather than local dev (see
  point 1) — real (non-mock) Claude calls, since that deployment has a real
  `ANTHROPIC_API_KEY` and shares V1's Supabase, unlike the local mock-mode
  testing described in `nova_v2/server/README.md`.
- No V2 build-plan doc — this file is the working plan for now.
- The hidden-nav / onboarding-gate TODOs inherited from V1 (see point 4) are
  unresolved on purpose.
