"""The notes REST surface, through FastAPI's TestClient.

Built on a bare FastAPI app with just the notes routers, so these tests don't
import main.py (and with it the Anthropic client and the Intent Surface).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from notes_support import *  # noqa: F401,F403 - fixtures
from notes_support import OTHER, USER

from app.api import notes as notes_api
from app.core.auth import AuthUser, current_user
from app.api import notes_pipeline as notes_pipeline_api
from app.notes_pipeline.processor import NotesPipelineProcessor
from app.notes_pipeline.summarise import MockSummariser
from app.store import notes, persona

NOTE_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def client(stores):
    """Signed in as USER; `client.act_as(OTHER)` switches account."""
    app = FastAPI()
    app.include_router(notes_api.router)
    app.include_router(notes_pipeline_api.router)
    notes.set_processor(NotesPipelineProcessor(summariser=MockSummariser()))
    acting = {"user": USER}
    app.dependency_overrides[current_user] = lambda: AuthUser(id=acting["user"], email=None)
    test_client = TestClient(app)
    test_client.act_as = lambda user_id: acting.update(user=user_id)
    return test_client


def post(client, **fields):
    body = {"id": NOTE_ID, "text": "ask tutor about Q3", "source": "device_voice", "kind": "quick", **fields}
    return client.post("/notes", json=body)


def test_post_is_idempotent(client):
    assert post(client).status_code == 200
    again = post(client, text="different text")
    assert again.status_code == 200
    assert again.json()["text"] == "ask tutor about Q3"
    assert len(client.get("/notes").json()) == 1


def test_list_rows_carry_preview_not_transcript(client):
    post(client, text="word " * 500, kind="capture")
    row = client.get("/notes").json()[0]
    assert len(row["preview"]) <= notes_api.PREVIEW_CHARS + 1
    assert "segments" not in row


def test_search_by_token(client):
    post(client)
    rows = client.get("/notes", params={"q": "Q3"}).json()
    assert rows[0]["id"] == NOTE_ID and "Q3" in rows[0]["snippet"]


def test_search_local_time_bounds(client):
    post(client, created_at="2026-09-22T23:30:00+00:00")  # 09:30 on the 23rd in Canberra
    params = {"since": "2026-09-23T00:00:00", "until": "2026-09-23T23:59:59", "utc_offset_minutes": 600}
    assert [r["id"] for r in client.get("/notes", params=params).json()] == [NOTE_ID]
    params["utc_offset_minutes"] = 0
    assert client.get("/notes", params=params).json() == []


def test_patch_then_search_finds_new_text(client):
    post(client, text="ask tutor about cue three")
    client.patch(f"/notes/{NOTE_ID}", json={"text": "ask tutor about Q3"})
    assert client.get("/notes", params={"q": "Q3"}).json()[0]["id"] == NOTE_ID


def test_delete_then_get_is_404(client):
    post(client)
    assert client.delete(f"/notes/{NOTE_ID}").status_code == 204
    assert client.get(f"/notes/{NOTE_ID}").status_code == 404
    assert client.delete(f"/notes/{NOTE_ID}").status_code == 404


def test_export_contains_everything(client):
    post(client)
    post(client, id="33333333-3333-3333-3333-333333333333", text="second note")
    exported = client.get("/notes/export").json()
    assert {n["text"] for n in exported} == {"ask tutor about Q3", "second note"}
    md = client.get("/notes/export", params={"format": "md"})
    assert md.headers["content-type"].startswith("text/markdown")
    assert "ask tutor about Q3" in md.text and "second note" in md.text


def test_delete_all_requires_confirm(client):
    post(client)
    assert client.delete("/notes").status_code == 400
    assert client.delete("/notes", params={"confirm": "true"}).json() == {"deleted": 1}


def test_long_dictation_is_chunked_and_summarised(client, stores):
    text = " ".join(f"Sentence number {i} about essay structure." for i in range(80))
    note = post(client, text=text, kind="dictation").json()
    assert note["summary_status"] == "done"
    assert note["summary"]["tldr"].startswith("[mock]")
    assert stores["notes"].chunks(NOTE_ID)


def test_summarise_never_is_respected(client):
    text = " ".join(f"Sentence {i}." for i in range(200))
    assert post(client, text=text, summarise="never").json()["summary_status"] == "none"


def test_resummarise_endpoint(client):
    post(client)
    note = client.post(f"/notes/{NOTE_ID}/summarise").json()
    assert note["summary_status"] == "done"


def test_promote_and_prune_after_fact_deleted(client):
    post(client, text="I am allergic to peanuts")
    fact_id = client.post(f"/notes/{NOTE_ID}/promote", json={"category": ["facts", "health"]}).json()["fact_id"]
    assert client.get(f"/notes/{NOTE_ID}").json()["promoted_fact_ids"] == [fact_id]
    persona.delete(USER, fact_id)
    assert client.get(f"/notes/{NOTE_ID}").json()["promoted_fact_ids"] == []


def test_device_note_leaves_content_free_episode(client, stores):
    post(client, text="secret lecture content", kind="capture",
         context={"calendar_title": "COMP2100 Lecture"}, duration_s=1500)
    rows = stores["memory"].rows
    assert [r["event_type"] for r in rows] == ["note_captured"]
    assert "secret" not in str(rows[0])
    assert rows[0]["event"]["calendar_title"] == "COMP2100 Lecture"


def test_markdown_share(client):
    post(client)
    assert "ask tutor about Q3" in client.get(f"/notes/{NOTE_ID}/markdown").text


# --- isolation between accounts --------------------------------------------------

def test_another_account_cannot_see_or_touch_my_note(client):
    post(client)
    client.act_as(OTHER)
    assert client.get("/notes").json() == []
    assert client.get("/notes", params={"q": "Q3"}).json() == []
    assert client.get("/notes/export").json() == []
    assert client.get(f"/notes/{NOTE_ID}").status_code == 404
    assert client.patch(f"/notes/{NOTE_ID}", json={"text": "mine now"}).status_code == 404
    assert client.post(f"/notes/{NOTE_ID}/summarise").status_code == 404
    assert client.delete(f"/notes/{NOTE_ID}").status_code == 404
    assert client.delete("/notes", params={"confirm": "true"}).json() == {"deleted": 0}

    client.act_as(USER)
    assert client.get(f"/notes/{NOTE_ID}").json()["text"] == "ask tutor about Q3"


def test_reusing_another_accounts_note_id_is_409(client):
    post(client)
    client.act_as(OTHER)
    assert post(client, text="probe").status_code == 409
