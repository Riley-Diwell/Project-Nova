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
import hmac
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
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
from app.tools.functions.notification_management import start_batchers, stop_batchers

from app.store import memory
from app.store import notes
from app.store import persona
from app.api import auth as auth_api
from app.api import me as me_api
from app.api import notes as notes_api
from app.api import notes_pipeline as notes_pipeline_api
from app.api import reminders as reminders_api
from app.api import canvas as canvas_api
from app.core import auth, llm
from app.core.auth import AuthUser, current_user
from app.core.request_user import bind_request_user
from app.notes_pipeline import NotesPipelineProcessor


# On a fresh container, the first request to touch Persona was paying ~28s
# inside persona_search, and the first to touch Supabase an extra ~550ms
# inside episode_open - both are lazy singletons (get_store()/get_client())
# that construct on first call rather than at import time, so whichever live
# request happens to go first eats the one-time setup cost. Fine for a
# long-lived process; bad on Cloud Run, which scales nova-v2 to zero when
# idle, so this recurred on every cold start rather than only once.
#
# _warm_up pays that cost here instead, right after startup - on a background
# thread, not before serving. Blocking startup on it made *every* request on a
# cold instance wait ~30s for the model, including a token refresh that needs
# no model at all, which made sign-out slow. Now light requests
# are answered at once, and only a request that needs Persona before the model
# is ready waits for it (persona's init lock makes that wait, not a 2nd load).
def _warm_up() -> None:
    """Force-construct Persona's embedder and the Supabase client before the
    server starts accepting requests. Non-fatal like every other Supabase/
    persona touch in this codebase - an unconfigured or unreachable backend
    must not stop the server from starting, it just leaves the cost where it
    was (paid on first real use instead of here). Skipped under
    NOVA_MOCK_LLM: local pipeline testing has no real Supabase/model to warm
    and shouldn't be stuck waiting on a 1.2GB model load for it."""
    if intent_surface.MOCK_LLM:
        print("[warmup] NOVA_MOCK_LLM set - skipping persona/db warm-up")
        return

    # A trivial real read, not just constructing the client - create_client()
    # mainly builds the Python wrapper and may not open the connection itself,
    # so only an actual request pays the TLS/auth cost episode_open otherwise
    # would. Goes through memory's own public API rather than naming the
    # table directly, same boundary memory.py itself asks callers to respect.
    # ping(), not a read: there's no signed-in user at start-up to read for.
    start = time.perf_counter()
    try:
        memory.ping()
        print(f"[warmup] Supabase connection ready ({(time.perf_counter() - start) * 1000:.0f}ms)")
    except Exception as e:
        print(f"[warmup] Supabase warm-up skipped: {e}")

    start = time.perf_counter()
    try:
        persona.get_store()
        print(f"[warmup] persona embedder ready ({(time.perf_counter() - start) * 1000:.0f}ms)")
    except Exception as e:
        print(f"[warmup] persona warm-up skipped: {e}")

    # One token from the model, so a stopped or unreachable model server shows
    # up in the log at start-up rather than on the first voice turn.
    start = time.perf_counter()
    try:
        llm.complete("Reply with OK.", "ping", max_tokens=4, timeout=120.0)
        print(f"[warmup] model {llm.MODEL} ready ({(time.perf_counter() - start) * 1000:.0f}ms)")
    except Exception as e:
        print(f"[warmup] model warm-up failed ({llm.LLM_BASE_URL}): {e}")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    threading.Thread(target=_warm_up, name="warm-up", daemon=True).start()
    # The notification_management tool gets one batcher per user, made on their
    # first turn - see tools/functions/notification_management.py.
    start_batchers()
    yield
    stop_batchers()


# initialise app
# Every route needs a signed-in user (a Supabase access token) unless it's in
# auth.PUBLIC_ROUTES - see core/auth.py.
# bind_request_user makes the verified user visible to Function tools deep inside
# /event (the memory tool reads the user's notes) - see core/request_user.py.
app = FastAPI(
    title="NOVA V1", lifespan=_lifespan,
    dependencies=[Depends(auth.require_user), Depends(bind_request_user)],
)

# Notes: the store's REST surface, and the pipeline that
# chunks and summarises long notes after the router stores them.
notes.set_processor(NotesPipelineProcessor())
app.include_router(notes_api.router)
app.include_router(notes_pipeline_api.router)

# Accounts.
auth.check_startup()
app.include_router(auth_api.router)
app.include_router(me_api.router)
app.include_router(reminders_api.router)
app.include_router(canvas_api.router)

_API_KEY = os.environ.get("NOVA_API_KEY", "").strip()
if not _API_KEY:
    # A deployed server must never run open.
    if auth.deployed():
        raise RuntimeError("NOVA_API_KEY is not set - refusing to start a deployed server without it.")
    print("[auth] NOVA_API_KEY not set - /event and friends are UNAUTHENTICATED. "
          "Fine for local dev; do not deploy publicly like this.")

# Reachable without the client key, for uptime checks.
_OPEN_PATHS = {"/health"}  # not /healthz: Cloud Run reserves paths ending in z


# The client key identifies a build, not a person (it ships in the APK), so a
# mismatch is 403. 401 is kept for bearer-token problems.
# compare_digest keeps the check constant-time.
@app.middleware("http")
async def _require_api_key(request: Request, call_next):
    if _API_KEY and request.url.path not in _OPEN_PATHS:
        supplied = request.headers.get("x-nova-api-key", "")
        if not hmac.compare_digest(supplied.encode(), _API_KEY.encode()):
            return JSONResponse(status_code=403, content={"detail": "missing or invalid X-Nova-Api-Key"})
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

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
def _open_episode(user_id: UUID, event: Event, user_state: UserState) -> str | None:
    try:
        episode_id = memory.append(user_id, {
            "event_type": event.type,
            "event": event.model_dump(mode="json"),
            # The reminder window stays out of the episode log (privacy): the
            # account's reminders live only in their own table (store/reminders.py),
            # and the Action input already records what Nova did.
            "user_state": user_state.model_dump(
                mode="json", exclude={"reminders", "reminders_pending_total"}
            ),
        })
        print(f"[memory] opened episode {episode_id} (event_type={event.type!r})")
        return episode_id
    except Exception as e:
        print(f"[memory] append skipped: {e}")
        return None

# log that looked like feedback and was not.
def _close_episode(user_id: UUID, intent: IntentResult) -> None:
    if not intent.episode_id:
        return
    try:
        memory.close(user_id, intent.episode_id, action={
            "actions": intent.actions,
            "speech": intent.speech,
        })
        print(f"[memory] closed episode {intent.episode_id} "
              f"({len(intent.actions)} action(s))")
    except Exception as e:
        print(f"[memory] episode close skipped: {e}")


def _to_response(intent: IntentResult | NeedMoreResult) -> EventResponse:
    if isinstance(intent, NeedMoreResult):
        request = {
            "type": intent.request_type,
            "from": intent.from_time,
            "to": intent.to_time,
        }
        if intent.include_done is not None:
            request["include_done"] = intent.include_done
        return NeedMoreOut(
            event_id=intent.event_id,
            session_id=intent.session_id,
            request=request,
        )
    return EventOut(
        event_id=intent.event_id,
        speech=intent.speech,
        actions=intent.actions,
        episode_id=intent.episode_id,
        confirmation=intent.confirmation,
        scheduled_departure=intent.scheduled_departure,
    )


# this is how I receive events from Riley
# app.post handles incoming HTTP POST requests
#
# Every route below is the signed-in user's: `user`
# comes from the verified token, and everything read or written is theirs.
# Plain `def`, not `async def`: the handlers make blocking calls (the model,
# Supabase), and FastAPI runs a plain def in its threadpool - as async they
# held the event loop, so one instance served one turn at a time.
@app.post("/event", response_model=EventResponse)
def receive_event(input_wrapper: InputWrapper, user: AuthUser = Depends(current_user)) -> EventResponse:
    print(f"[/event] received text: {getattr(input_wrapper.event, 'text', None)!r}")
    us = input_wrapper.user_state
    print(f"[/event] calendar_ctx={us.calendar_ctx!r} "
          f"current_events={len(us.current_events)} upcoming_events={len(us.upcoming_events)}")
    episode_id = _open_episode(user.id, input_wrapper.event, us)
    intent = intent_surface.run(user.id, input_wrapper.user_state, input_wrapper.event, episode_id)
    if isinstance(intent, IntentResult):
        _close_episode(user.id, intent)
    return _with_consolidation_hint(user.id, _to_response(intent))


def _with_consolidation_hint(user_id: UUID, response: EventResponse) -> EventResponse:
    """Tell the phone when it's time to learn from recent activity. Memoised
    and non-fatal: a turn never waits on, or fails because of, this."""
    if isinstance(response, EventOut):
        try:
            from app.store import consolidation

            response.consolidation_due = consolidation.due(user_id).due
        except Exception as e:
            print(f"[consolidation] due check skipped: {e}")
    return response


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
def report_outcome(report: OutcomeIn, user: AuthUser = Depends(current_user)) -> None:
    """Record the user's verdict on one of their turns and move the gain of
    what it did. Another user's episode id changes nothing."""
    try:
        memory.close(user.id, report.episode_id, outcome=report.outcome)
    except Exception as e:
        # Non-fatal like every other Memory touch, but the reinforcement below
        # still runs: the gain move is the part the user will actually notice.
        print(f"[memory] outcome write skipped: {e}")

    moved = intent_surface.reinforce_episode(user.id, report.episode_id, report.outcome)
    print(f"[gain] episode {report.episode_id} {report.outcome}: moved {moved}")


# --- per-tool gain (Section 5.7) ---------------------------------------------
# The Android app's Gain tab (ui/screens/GainScreen.kt) reads these to draw one
# dial per Function tool and writes back what the user dials in. Gain is both
# user-set here and moved by reinforcement above; an override, while present,
# wins over whatever reinforcement has learned.

@app.get("/tools/gain", response_model=list[ToolGainOut])
def get_tool_gains(user: AuthUser = Depends(current_user)) -> list[dict[str, Any]]:
    return intent_surface.gain_overrides(user.id).view_all()


# PUT, not POST: setting a tool's override to x is idempotent. A null override
# clears it, reverting that tool to its learned value.
@app.put("/tools/gain/{tool_name}", response_model=ToolGainOut)
def put_tool_gain(
    tool_name: str, update: ToolGainUpdate, user: AuthUser = Depends(current_user)
) -> dict[str, Any]:
    try:
        return intent_surface.gain_overrides(user.id).set(tool_name, update.override)
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
def get_persona_graph(
    response: Response,
    min_similarity: float = persona.DEFAULT_MIN_SIMILARITY,
    max_links: int = persona.DEFAULT_MAX_LINKS,
    clusters: bool = False,
    if_none_match: str | None = Header(default=None),
    user: AuthUser = Depends(current_user),
) -> Any:
    """Persona as nodes and edges, for the Knowledge Map tab.

    min_similarity is a query parameter because the right density is a taste
    question and will drift as the store grows - see persona/graph.py for the
    measurements behind the default.

    Carries an ETag. The phone keeps the last graph and sends its tag back as
    If-None-Match; if nothing has changed it gets a bodyless 304, and the server
    never loads the embeddings or links a single pair (persona.graph_etag).

    `clusters=true` groups facts by meaning (persona/clusters.py) for the map's
    subheadings; where grouping isn't available the graph comes back grouped
    by category as before, and the phone groups by that.

    Also says whether NOVA is due to learn from recent activity, in
    X-Nova-Consolidation (due / running / idle) and X-Nova-Last-Consolidated -
    on a 304 too, since opening the map is one of the moments the phone
    triggers a pass.
    """
    view = persona.cluster_view(user.id) if clusters else None
    etag = persona.graph_etag(user.id, min_similarity=min_similarity, max_links=max_links, clusters=view)
    # private: one person's beliefs, never for a shared cache. no-cache: always ask first.
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", **_consolidation_headers(user.id)}
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)

    graph = persona.knowledge_graph(user.id, min_similarity=min_similarity, max_links=max_links, clusters=view)
    print(f"[persona] graph {graph.stats()}")
    response.headers.update(headers)
    return {**graph.model_dump(mode="json"), "stats": graph.stats()}


def _consolidation_headers(user_id: UUID) -> dict[str, str]:
    """The map's view of background learning. Empty where it's unavailable -
    the phone then just doesn't trigger from here."""
    try:
        from app.store import consolidation

        check = consolidation.due(user_id)
    except Exception as e:
        print(f"[consolidation] due check skipped: {e}")
        return {}
    headers = {"X-Nova-Consolidation": check.status}
    if check.last_run_at:
        headers["X-Nova-Last-Consolidated"] = check.last_run_at.isoformat()
    return headers


@app.get("/persona/search")
def search_persona(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    """The Knowledge Map's search bar: this user's beliefs by meaning and by
    keyword (persona.search - era-memory's hybrid ranking), each with its group,
    plus the group the hits concentrate in, for the map to fly to.

    A hit is kept if it is close in meaning (cosine >= SEARCH_MIN_SIMILARITY) or
    shares a word with the query at all - "COMP2100" should find its course
    even though the embedding barely sees it. `match` says which.
    """
    matches = persona.search(user.id, persona.PersonaQuery(text=q, limit=limit))
    view = persona.cluster_view(user.id)
    membership = view.membership if view else {}
    hits = [
        {
            "fact_id": m.fact.id,
            "text": m.fact.text,
            "similarity": round(m.similarity, 3),
            "lexical": round(m.lexical, 4),
            "score": m.score,
            "cluster_id": membership.get(m.fact.id or ""),
            "match": "meaning" if m.similarity >= SEARCH_MIN_SIMILARITY else "keyword",
        }
        for m in matches
        if m.similarity >= SEARCH_MIN_SIMILARITY or m.lexical > 0
    ]
    weight: dict[str, float] = {}
    for h in hits:
        if h["cluster_id"]:
            weight[h["cluster_id"]] = weight.get(h["cluster_id"], 0.0) + h["score"]
    target = max(weight, key=lambda c: weight[c]) if weight else None
    print(f"[persona] search {q[:40]!r}: {len(hits)} hit(s), target={target}")
    return {"hits": hits, "target_cluster": target}


# Query-to-statement cosine runs lower than fact-to-fact (see intent_surface's
# PERSONA_MIN_SIMILARITY, 0.35, for the measurements), so a search takes hits
# a fair way below the grouping threshold. A starting point - calibrate on real
# queries. Keyword hits pass regardless.
SEARCH_MIN_SIMILARITY = 0.5


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 If-None-Match: a list of tags, compared weakly, or "*"."""
    if not if_none_match:
        return False
    tags = [t.strip().removeprefix("W/") for t in if_none_match.split(",")]
    return "*" in tags or etag in tags


@app.get("/persona")
def list_persona(user: AuthUser = Depends(current_user)) -> list[dict[str, Any]]:
    """Every belief, newest first - the list view behind the map."""
    return [f.model_dump(mode="json") for f in persona.all_facts(user.id)]


@app.patch("/persona/{fact_id}")
def edit_persona_fact(
    fact_id: str, edit: FactEdit, user: AuthUser = Depends(current_user)
) -> dict[str, Any]:
    """Correct a belief in place. Re-embeds, so an edited fact is findable by
    what it now says rather than what it used to. Someone else's fact id is a
    404, the same as one that doesn't exist.

    Through persona.remember(), dated now: the edited belief keeps its id, any
    other belief it now duplicates is merged into it, and any it contradicts
    is removed. The response is the fact plus `action` and `superseded` (the
    beliefs the edit overruled), so the map can drop their nodes."""
    if not _is_uuid(fact_id):
        raise HTTPException(status_code=404, detail=f"unknown fact: {fact_id!r}")
    try:
        current = persona.get(user.id, fact_id)
    except persona.FactNotFound:
        raise HTTPException(status_code=404, detail=f"unknown fact: {fact_id!r}")

    updated = current.model_copy(update={
        "text": edit.text if edit.text is not None else current.text,
        "category": edit.category if edit.category is not None else current.category,
        # Correcting a belief makes it something the user stated, whatever it
        # was before - they have overruled whatever NOVA inferred.
        "metadata": {**(current.metadata or {}), "source": "stated", "edited": True},
    })
    try:
        result = persona.remember(user.id, updated, stated_at=datetime.now(timezone.utc))
        # What NOVA knows just changed by hand: older conversation stops being
        # context, so the old version can't be picked back up from it.
        intent_surface.reset_context(user.id)
        return {
            **persona.get(user.id, fact_id).model_dump(mode="json"),
            "action": result.action.value,
            "superseded": [s.model_dump(mode="json") for s in result.superseded],
        }
    except persona.FactNotFound:  # deleted in between
        raise HTTPException(status_code=404, detail=f"unknown fact: {fact_id!r}")


@app.delete("/persona/{fact_id}", status_code=204)
def delete_persona_fact(fact_id: str, user: AuthUser = Depends(current_user)) -> None:
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
    persona.delete(user.id, fact_id)  # a no-op for someone else's id
    # "Always reply in pirate speak", deleted, must not carry on through the
    # pirate replies still sitting in recent conversation.
    intent_surface.reset_context(user.id)
    print(f"[persona] deleted {fact_id}")


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except ValueError:
        return False


@app.post("/persona/consolidate")
def run_consolidation(
    preview: bool = False, if_due: bool = False, user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    """Turn what has happened into what is true about the user.

    There is no button for this any more: it happens on its own. /event and the
    map's graph fetch say when a pass is due, and the phone's background worker
    calls this with `if_due=true` - which runs an incremental pass over only
    the episodes since the last one, or does nothing if none is due. The phone
    makes the call because Cloud Run keeps a request's CPU only while it's
    open. What was learned still shows: the map's "Learning…" line while it
    runs, and a "new" marker on what it added.

    Without `if_due`, a full pass over the whole log (manual use). `preview=true`
    returns exactly what a full pass would write without writing it.
    """
    from app.store.consolidation import consolidate, preview_statements, run_if_due
    from app.store.consolidation import preview as preview_trends

    if if_due and not preview:
        run = run_if_due(user.id)
        print(f"[consolidation] if-due: ran={run.ran} running={run.running} "
              f"episodes={run.episodes_read}")
        return run.model_dump(mode="json")

    reconciled: list[dict[str, Any]] = []
    if preview:
        derived = [f.model_dump(mode="json") for f in preview_trends(user.id)]
        stated = [f.model_dump(mode="json") for f in preview_statements(user.id)]
    else:
        result = consolidate(user.id)
        derived = [f.model_dump(mode="json") for f in result["derived"]]
        stated = [f.model_dump(mode="json") for f in result["stated"]]
        reconciled = [r.model_dump(mode="json") for r in result["reconciled"]]

    print(f"[consolidation] {'preview' if preview else 'run'}: "
          f"{len(derived)} derived, {len(stated)} stated")
    return {"preview": preview, "derived": derived, "stated": stated, "reconciled": reconciled}


@app.post("/persona/reconcile")
def run_reconcile(preview: bool = True, user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    """Sweep this user's whole Persona for duplicates and contradictions and
    resolve them the way persona.remember() does for each new belief - most
    recent wins, duplicates merge. For beliefs written before that existed,
    and anything a failed judge call left unreconciled.

    Previews by default (the sweep runs over an in-memory copy); pass
    preview=false to apply. Returns only the beliefs something happened to.
    """
    results = persona.reconcile_all(user.id, dry_run=preview)
    print(f"[persona] reconcile {'preview' if preview else 'run'}: {len(results)} change(s)")
    return {"preview": preview, "changes": [r.model_dump(mode="json") for r in results]}


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
def list_audit(
    limit: int = 50,
    since: str | None = None,
    until: str | None = None,
    tool: str | None = None,
    q: str | None = None,
    user: AuthUser = Depends(current_user),
) -> list[dict[str, Any]]:
    filtering = bool(tool or q)
    fetch_limit = _AUDIT_FILTERED_FETCH_CAP if filtering else min(limit, _AUDIT_DEFAULT_FETCH_CAP)
    try:
        episodes = memory.recent_all(user.id, fetch_limit, since=since, until=until)
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
def continue_event(input_wrapper: ContinueWrapper, user: AuthUser = Depends(current_user)) -> EventResponse:
    print(f"[/event/continue] session_id={input_wrapper.session_id!r}")
    try:
        # 404 for someone else's session too - see intent_surface.resume.
        intent = intent_surface.resume(user.id, input_wrapper.session_id, input_wrapper.result)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    # The turn that started at /event may only finish here, so this is the
    # other place an episode can close.
    if isinstance(intent, IntentResult):
        _close_episode(user.id, intent)
    return _with_consolidation_hint(user.id, _to_response(intent))
