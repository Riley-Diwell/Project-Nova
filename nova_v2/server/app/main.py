"""
main.py - Section 5.3: Intent Surface  (Georgia)

STATUS: wip

WHAT THIS FILE IS
FastAPI entrypoint. Exposes POST /event as the single wire seam between
Riley's Android client and the backend.

Android computes UserState on-device (UserStateCollector.kt, ~19 fused
signals) and posts it directly alongside every Event - the backend does not
re-derive state from raw signals, so there is no separate Signals payload.
That makes /event the first place UserState exists backend-side, so this is
where each episode is appended to the Memory log (see _log_episode).

Run (from nova_v2/server/, not server/app/):
    uvicorn app.main:app --reload

WHO USES THIS
- Riley: POSTs events here from Android
- Georgia:
"""

# import necessary libraries
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# import nova libraries
from app.schemas.audit import AuditEntryOut
from app.schemas.event import Event
from app.schemas.event_out import EventOut, EventResponse, NeedMoreOut
from app.schemas.tool_gain import ToolGainOut, ToolGainUpdate
from app.schemas.user_state import UserState
from app import intent_surface
from app.intent_surface import IntentResult, NeedMoreResult
from app.tools.core import narration
from app.tools.core.action import Action
from app.tools.functions.notification_batcher import NotificationBatcher
from app.tools.functions.notification_management import register_batcher

from app.store import memory
from app.store import persona


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Shared with the notification_management tool (see intent_surface.py's
    # ToolRegistry) - without this, the tool has no batcher to query.
    batcher = NotificationBatcher()
    batcher.start()
    register_batcher(batcher)
    yield
    batcher.stop()


# initialise app
app = FastAPI(title="NOVA V1", lifespan=_lifespan)

_API_KEY = os.environ.get("NOVA_API_KEY", "").strip()
if not _API_KEY:
    print("[auth] NOVA_API_KEY not set - /event and friends are UNAUTHENTICATED. "
          "Fine for local dev; do not deploy publicly like this.")


@app.middleware("http")
async def _require_api_key(request: Request, call_next):
    if _API_KEY and request.headers.get("x-nova-api-key") != _API_KEY:
        return JSONResponse(status_code=401, content={"detail": "missing or invalid X-Nova-Api-Key"})
    return await call_next(request)

# combine event and user_state into one wrapper - matches what Android posts
class InputWrapper(BaseModel):
    event: Event
    user_state: UserState

# what Android posts back to /event/continue once it has resolved a
# need_more request on-device (e.g. queried the calendar for the requested
# range). result is whatever shape the request type calls for - opaque here,
# intent_surface.resume() feeds it straight back to Claude as the paused
# tool's result.
class ContinueWrapper(BaseModel):
    session_id: str
    result: Any


# Android determines UserState on-device, so POST /event is where it first
# reaches the backend - i.e. the point the episodic Memory log is meant to
# capture (schema.sql: "one row per event, event + the User State it produced").
# Logged before the loop runs so an episode survives a failing Claude call, and
# so intent_surface.run() can read prior episodes back as context.
# Non-fatal: an unconfigured or unreachable Supabase must not fail /event.
def _open_episode(event: Event, user_state: UserState) -> str | None:
    try:
        episode_id = memory.append({
            "event_type": event.type,
            "event": event.model_dump(mode="json"),
            "user_state": user_state.model_dump(mode="json"),
        })
        print(f"[memory] opened episode {episode_id} (event_type={event.type!r})")
        return episode_id
    except Exception as e:
        print(f"[memory] append skipped: {e}")
        return None

# log that looked like feedback and was not.
def _close_episode(intent: IntentResult) -> None:
    if not intent.episode_id:
        return
    try:
        memory.close(intent.episode_id, action={
            "actions": intent.actions,
            "speech": intent.speech,
        })
        print(f"[memory] closed episode {intent.episode_id} "
              f"({len(intent.actions)} action(s))")
    except Exception as e:
        print(f"[memory] episode close skipped: {e}")


def _to_response(intent: IntentResult | NeedMoreResult) -> EventResponse:
    if isinstance(intent, NeedMoreResult):
        return NeedMoreOut(
            event_id=intent.event_id,
            session_id=intent.session_id,
            request={
                "type": intent.request_type,
                "from": intent.from_time,
                "to": intent.to_time,
            },
        )
    return EventOut(
        event_id=intent.event_id,
        speech=intent.speech,
        actions=intent.actions,
        episode_id=intent.episode_id,
        confirmation=intent.confirmation,
    )


# this is how I receive events from Riley
# app.post handles incoming HTTP POST requests
@app.post("/event", response_model=EventResponse)
async def receive_event(input_wrapper: InputWrapper) -> EventResponse:
    print(f"[/event] received text: {getattr(input_wrapper.event, 'text', None)!r}")
    us = input_wrapper.user_state
    print(f"[/event] calendar_ctx={us.calendar_ctx!r} "
          f"current_events={len(us.current_events)} upcoming_events={len(us.upcoming_events)}")
    episode_id = _open_episode(input_wrapper.event, us)
    intent = intent_surface.run(input_wrapper.user_state, input_wrapper.event, episode_id)
    if isinstance(intent, IntentResult):
        _close_episode(intent)
    return _to_response(intent)


# --- the turn's Outcome (Sections 5.5, 5.7) ----------------------------------
# The reinforcement signal, and the reason episodic_memory.outcome exists.
#
# The phone reports how the turn ended: `accepted` when TTS ran to completion,
# `rejected` when the user pressed stop and talked over it. Nothing else is a
# verdict - in particular the gate's own decision is not, which is what an
# earlier draft wrongly wrote into this column.
#
# Barge-in is the only negative signal V1 collects, deliberately: a thumbs-down
# button would be a phone interaction, and removing phone interactions is the
# entire product. Stop is a control the user wants for its own sake, so reading
# it as feedback costs them nothing. See docs/adr/0002.

class OutcomeIn(BaseModel):
    episode_id: str
    outcome: Literal["accepted", "rejected"]


@app.post("/event/outcome", status_code=204)
async def report_outcome(report: OutcomeIn) -> None:
    """Record the user's verdict on a turn and move the gain of what it did."""
    try:
        memory.close(report.episode_id, outcome=report.outcome)
    except Exception as e:
        # Non-fatal like every other Memory touch, but the reinforcement below
        # still runs: the gain move is the part the user will actually notice.
        print(f"[memory] outcome write skipped: {e}")

    moved = intent_surface.reinforce_episode(report.episode_id, report.outcome)
    print(f"[gain] episode {report.episode_id} {report.outcome}: moved {moved}")


# --- per-tool gain (Section 5.7) ---------------------------------------------
# The Android app's Gain tab (ui/screens/GainScreen.kt) reads these to draw one
# dial per Function tool and writes back what the user dials in. Gain is both
# user-set here and moved by reinforcement above; an override, while present,
# wins over whatever reinforcement has learned.

@app.get("/tools/gain", response_model=list[ToolGainOut])
async def get_tool_gains() -> list[dict[str, Any]]:
    return intent_surface.GAIN_OVERRIDES.view_all()


# PUT, not POST: setting a tool's override to x is idempotent. A null override
# clears it, reverting that tool to its learned value.
@app.put("/tools/gain/{tool_name}", response_model=ToolGainOut)
async def put_tool_gain(tool_name: str, update: ToolGainUpdate) -> dict[str, Any]:
    try:
        return intent_surface.GAIN_OVERRIDES.set(tool_name, update.override)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown tool: {tool_name!r}")


# --- Knowledge Map (Section 5.6) ---------------------------------------------
# Persona's HTTP surface. This is the visible face of the Agency and Privacy
# pillars: the user gets to see what NOVA believes about them, correct it, and
# delete it. Read-only endpoints would miss the point - REQ1 is view/edit/
# delete/export, and a store you can only look at is not user-controlled.

class FactEdit(BaseModel):
    """A Knowledge Map edit. Both fields optional: renaming a belief and
    refiling it are separate gestures in the UI."""
    text: str | None = None
    category: list[str] | None = None


@app.get("/persona/graph")
async def get_persona_graph(
    min_similarity: float = persona.DEFAULT_MIN_SIMILARITY,
    max_links: int = persona.DEFAULT_MAX_LINKS,
) -> dict[str, Any]:
    """Persona as nodes and edges, for the Knowledge Map tab.

    min_similarity is a query parameter because the right density is a taste
    question and will drift as the store grows - see persona/graph.py for the
    measurements behind the default.
    """
    graph = persona.knowledge_graph(min_similarity=min_similarity, max_links=max_links)
    print(f"[persona] graph {graph.stats()}")
    return {**graph.model_dump(mode="json"), "stats": graph.stats()}


@app.get("/persona")
async def list_persona() -> list[dict[str, Any]]:
    """Every belief, newest first - the list view behind the map."""
    return [f.model_dump(mode="json") for f in persona.all_facts()]


@app.patch("/persona/{fact_id}")
async def edit_persona_fact(fact_id: str, edit: FactEdit) -> dict[str, Any]:
    """Correct a belief in place. Re-embeds, so an edited fact is findable by
    what it now says rather than what it used to."""
    try:
        current = persona.get(fact_id)
    except persona.FactNotFound:
        raise HTTPException(status_code=404, detail=f"unknown fact: {fact_id!r}")

    updated = current.model_copy(update={
        "text": edit.text if edit.text is not None else current.text,
        "category": edit.category if edit.category is not None else current.category,
        # Correcting a belief makes it something the user stated, whatever it
        # was before - they have overruled whatever NOVA inferred.
        "metadata": {**(current.metadata or {}), "source": "stated", "edited": True},
    })
    persona.upsert(updated)
    return persona.get(fact_id).model_dump(mode="json")


@app.delete("/persona/{fact_id}", status_code=204)
async def delete_persona_fact(fact_id: str) -> None:
    """Forget a belief (Privacy pillar / REQ1). Deliberately unconditional - the
    user does not have to justify it, and it is gone from the vector store as
    well as the list. A tombstone keeps it gone: consolidation would otherwise
    re-derive it on its next run (docs/adr/0003).

    Only a Fact id is deletable. The Knowledge Map's graph also contains category
    nodes, whose ids are ontology paths ("cat:routines/places") rather than
    UUIDs - those reach Postgres as malformed input, and a 400 says so instead of
    letting it surface as a 500.
    """
    if not _is_uuid(fact_id):
        raise HTTPException(
            status_code=400,
            detail=f"not a fact id: {fact_id!r}. Categories are not separately "
                   f"deletable - they disappear with the last fact filed under them.",
        )
    persona.delete(fact_id)
    print(f"[persona] deleted {fact_id}")


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except ValueError:
        return False


@app.post("/persona/consolidate")
async def run_consolidation(preview: bool = False) -> dict[str, Any]:
    """Turn what has happened repeatedly into what is true about the user.

    Driven from the Knowledge Map rather than a timer, because this is the one
    moment the map exists to show: Episodes the user can scroll past becoming
    beliefs NOVA will act on months later. A background job would do the same
    work invisibly, and the visibility is the product.

    `preview=true` returns exactly what a real run would write without writing
    it - the same code path, so what is shown is what would land.
    """
    from app.store.consolidation import consolidate, preview_statements
    from app.store.consolidation import preview as preview_trends

    if preview:
        derived = [f.model_dump(mode="json") for f in preview_trends()]
        stated = [f.model_dump(mode="json") for f in preview_statements()]
    else:
        result = consolidate()
        derived = [f.model_dump(mode="json") for f in result["derived"]]
        stated = [f.model_dump(mode="json") for f in result["stated"]]

    print(f"[consolidation] {'preview' if preview else 'run'}: "
          f"{len(derived)} derived, {len(stated)} stated")
    return {"preview": preview, "derived": derived, "stated": stated}


# --- Audit log (Autonomy pillar) ---------------------------------------------
# The Android app's Audit tab (ui/screens/AuditLogScreen.kt): every automated
# action NOVA has taken, and why, so the user can review and verify it. Reads
# episodic_memory back out - nothing here writes to it - and flattens each
# episode's action.actions[] into one row per Tool call, newest episode first.
# Non-fatal like every other Memory touch (_open_episode, _close_episode): an
# unconfigured or unreachable Supabase must not fail /audit, just show nothing.
#
# `tool` and `q` filter after flattening (they depend on the computed
# `summary`/`context` text, not a raw column), so when either is set the
# episode fetch widens past the requested `limit` - otherwise a filter could
# legitimately return fewer than `limit` matches while older matching entries
# still existed just outside a `limit`-sized window.
_AUDIT_FILTERED_FETCH_CAP = 500
_AUDIT_DEFAULT_FETCH_CAP = 200


@app.get("/audit", response_model=list[AuditEntryOut])
async def list_audit(
    limit: int = 50,
    since: str | None = None,
    until: str | None = None,
    tool: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    filtering = bool(tool or q)
    fetch_limit = _AUDIT_FILTERED_FETCH_CAP if filtering else min(limit, _AUDIT_DEFAULT_FETCH_CAP)
    try:
        episodes = memory.recent_all(fetch_limit, since=since, until=until)
    except Exception as e:
        print(f"[memory] audit read skipped: {e}")
        return []

    entries: list[dict[str, Any]] = []
    for episode in episodes:
        action_column = episode.get("action")
        if not isinstance(action_column, dict):
            continue
        speech = action_column.get("speech")
        event_type = episode.get("event_type", "unknown")
        event = episode.get("event") if isinstance(episode.get("event"), dict) else {}
        context = narration.describe_event(event_type, event)

        for action in Action.from_episode(action_column):
            if tool and action.tool != tool:
                continue
            entry = {
                "episode_id": str(episode["id"]),
                "occurred_at": episode.get("created_at"),
                "event_type": event_type,
                "context": context,
                "speech": speech,
                "summary": narration.describe_action(action.tool, action.input, action.ran),
                **action.for_wire(),
            }
            if q and not narration.matches_query(entry, q):
                continue
            entries.append(entry)
            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break

    return entries


# Android posts here after resolving a need_more request from /event (see
# intent_surface.py's CLIENT_TOOLS) - resumes the same paused Claude conversation.
@app.post("/event/continue", response_model=EventResponse)
async def continue_event(input_wrapper: ContinueWrapper) -> EventResponse:
    print(f"[/event/continue] session_id={input_wrapper.session_id!r}")
    try:
        intent = intent_surface.resume(input_wrapper.session_id, input_wrapper.result)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    # The turn that started at /event may only finish here, so this is the
    # other place an episode can close.
    if isinstance(intent, IntentResult):
        _close_episode(intent)
    return _to_response(intent)
