"""The X-Nova-Api-Key client-key check in main.py.

Imports main.py under NOVA_MOCK_LLM, so no model or Supabase is touched. The
key is read at import time, so tests patch main._API_KEY directly.
"""
import importlib
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("NOVA_MOCK_LLM", "1")
from app import main  # noqa: E402

KEY = "test-client-key"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "_API_KEY", KEY)
    # No lifespan: the check runs before any route, so no warm-up or batcher needed.
    return TestClient(main.app)


def test_missing_key_is_403(client):
    assert client.get("/persona/graph").status_code == 403


def test_wrong_key_is_403(client):
    assert client.get("/persona/graph", headers={"X-Nova-Api-Key": "wrong"}).status_code == 403


def test_non_ascii_key_is_403_not_500(client):
    assert client.get("/persona/graph", headers={"X-Nova-Api-Key": "ké".encode("latin-1")}).status_code == 403


def test_right_key_passes_the_check(client):
    r = client.get("/health", headers={"X-Nova-Api-Key": KEY})
    assert r.status_code == 200


def test_healthz_needs_no_key(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_no_key_configured_leaves_local_dev_open(monkeypatch):
    monkeypatch.setattr(main, "_API_KEY", "")
    assert TestClient(main.app).get("/health").status_code == 200


def test_refuses_to_start_open_on_cloud_run(monkeypatch):
    monkeypatch.delenv("NOVA_API_KEY", raising=False)
    monkeypatch.setenv("K_SERVICE", "nova-v2")
    try:
        with pytest.raises(RuntimeError, match="NOVA_API_KEY"):
            importlib.reload(main)
    finally:
        # Put the module back together for any test that runs after this one.
        monkeypatch.delenv("K_SERVICE")
        importlib.reload(main)
