"""Accounts: token checks and /auth, /me.

Tokens are signed here with a throwaway ES256 key standing in for the Supabase
project's JWKS, and GoTrue is an httpx MockTransport - nothing leaves the
machine. Built on a bare FastAPI app with just the auth routers, like the
notes tests, so the client-key middleware isn't in the way.
"""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.api import me as me_api
from app.core import auth, gotrue
from app.core.config import settings
from app.store import profile
from app.store.profile import InMemoryProfileStore

USER_ID = str(uuid.uuid4())
PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
PUBLIC_KEY = PRIVATE_KEY.public_key()


def make_token(key=PRIVATE_KEY, alg="ES256", **overrides):
    claims = {"sub": USER_ID, "email": "a@example.com", "aud": "authenticated",
              "role": "authenticated", "exp": int(time.time()) + 3600, **overrides}
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "test"})


def hs256_with_public_key():
    """The classic key-confusion attack: HS256, keyed with the public key's
    bytes. Built by hand, since PyJWT won't sign it."""
    def b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=")
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "test"}).encode())
    body = b64(json.dumps({"sub": USER_ID, "aud": "authenticated",
                           "exp": int(time.time()) + 3600}).encode())
    pem = PUBLIC_KEY.public_bytes(serialization.Encoding.PEM,
                                  serialization.PublicFormat.SubjectPublicKeyInfo)
    sig = b64(hmac.new(pem, header + b"." + body, hashlib.sha256).digest())
    return (header + b"." + body + b"." + sig).decode()


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return jwt.PyJWK.from_dict(json.loads(jwt.algorithms.ECAlgorithm.to_jwk(PUBLIC_KEY)))


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    for name in ("SUPABASE_JWT_SECRET", "NOVA_AUTH_DISABLED", "K_SERVICE"):
        monkeypatch.delenv(name, raising=False)
    settings.cache_clear()
    monkeypatch.setattr(auth, "_jwks_client", lambda: FakeJwks())
    profile.set_store(InMemoryProfileStore())  # GET /me reads the profile
    yield
    profile.set_store(None)
    settings.cache_clear()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(auth_api.router)
    app.include_router(me_api.router)
    return TestClient(app)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# --- token checks -----------------------------------------------------------

def test_valid_token(client):
    r = client.get("/me", headers=bearer(make_token()))
    assert r.status_code == 200
    assert r.json() == {"user_id": USER_ID, "email": "a@example.com", "profile": None}


@pytest.mark.parametrize("token", [
    pytest.param(lambda: make_token(exp=int(time.time()) - 120), id="expired"),
    pytest.param(lambda: make_token(aud="anon"), id="wrong-audience"),
    pytest.param(lambda: make_token(exp=None), id="no-exp"),
    pytest.param(lambda: make_token(sub=None), id="no-sub"),
    pytest.param(lambda: make_token(sub="not-a-uuid"), id="bad-sub"),
    pytest.param(lambda: make_token(key=ec.generate_private_key(ec.SECP256R1())), id="other-key"),
    pytest.param(lambda: make_token(key=None, alg="none"), id="alg-none"),
    pytest.param(lambda: hs256_with_public_key(), id="hs256-with-public-key"),
    pytest.param(lambda: "not.a.jwt", id="garbage"),
])
def test_bad_tokens_are_401(client, token):
    r = client.get("/me", headers=bearer(token()))
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_missing_token_is_401(client):
    r = client.get("/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_expiry_leeway_tolerates_small_clock_skew(client):
    assert client.get("/me", headers=bearer(make_token(exp=int(time.time()) - 10))).status_code == 200


def test_unreachable_jwks_is_503_not_401(client, monkeypatch):
    class Down:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientConnectionError("down")
    monkeypatch.setattr(auth, "_jwks_client", lambda: Down())
    assert client.get("/me", headers=bearer(make_token())).status_code == 503


def test_hs256_secret_for_self_hosted(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s" * 32)
    settings.cache_clear()
    assert client.get("/me", headers=bearer(make_token(key="s" * 32, alg="HS256"))).status_code == 200
    # With a secret configured, asymmetric tokens are no longer accepted.
    assert client.get("/me", headers=bearer(make_token())).status_code == 401


def test_auth_disabled_gives_the_dev_user(client, monkeypatch):
    monkeypatch.setenv("NOVA_AUTH_DISABLED", "1")
    settings.cache_clear()
    r = client.get("/me")
    assert r.status_code == 200
    assert r.json()["user_id"] == "00000000-0000-0000-0000-000000000001"


def test_auth_disabled_refuses_to_start_on_cloud_run(monkeypatch):
    monkeypatch.setenv("NOVA_AUTH_DISABLED", "1")
    monkeypatch.setenv("K_SERVICE", "nova-v2")
    settings.cache_clear()
    with pytest.raises(RuntimeError, match="NOVA_AUTH_DISABLED"):
        auth.check_startup()


# --- GoTrue pass-through ----------------------------------------------------

GOTRUE_SESSION = {
    "access_token": "at", "refresh_token": "rt", "expires_at": 1_900_000_000, "expires_in": 3600,
    "token_type": "bearer", "user": {"id": USER_ID, "email": "a@example.com", "role": "authenticated"},
}


@pytest.fixture
def gotrue_replies(monkeypatch):
    """Point core/gotrue.py at a fake. Set .reply = (status, json) and read .requests."""
    class Fake:
        reply = (200, GOTRUE_SESSION)
        requests: list[httpx.Request] = []

    def handler(request):
        Fake.requests.append(request)
        status, body = Fake.reply
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    monkeypatch.setattr(gotrue, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    Fake.requests = []
    return Fake


CREDS = {"email": "a@example.com", "password": "correct-horse"}


def test_login_returns_session(client, gotrue_replies):
    r = client.post("/auth/login", json=CREDS)
    assert r.status_code == 200
    assert r.json() == {"access_token": "at", "refresh_token": "rt", "expires_at": 1_900_000_000,
                        "user": {"id": USER_ID, "email": "a@example.com"}}
    sent = gotrue_replies.requests[0]
    assert str(sent.url) == "https://test.supabase.co/auth/v1/token?grant_type=password"
    assert sent.headers["apikey"] == "anon"


def test_login_bad_password_passes_gotrue_error_through(client, gotrue_replies):
    gotrue_replies.reply = (400, {"code": 400, "error_code": "invalid_credentials",
                                  "msg": "Invalid login credentials"})
    r = client.post("/auth/login", json=CREDS)
    assert r.status_code == 400
    assert r.json()["detail"] == {"code": "invalid_credentials", "message": "Invalid login credentials"}


def test_short_password_rejected_before_gotrue(client, gotrue_replies):
    assert client.post("/auth/login", json={**CREDS, "password": "short"}).status_code == 422
    assert gotrue_replies.requests == []


def test_signup_with_autoconfirm_returns_session(client, gotrue_replies):
    r = client.post("/auth/signup", json=CREDS)
    assert r.status_code == 200
    assert r.json()["access_token"] == "at"


def test_signup_needing_confirmation(client, gotrue_replies):
    gotrue_replies.reply = (200, {"id": USER_ID, "email": "a@example.com"})
    r = client.post("/auth/signup", json=CREDS)
    assert r.status_code == 200
    assert r.json() == {"confirmation_required": True}


def test_refresh_refused_is_always_401(client, gotrue_replies):
    gotrue_replies.reply = (400, {"error_code": "refresh_token_already_used", "msg": "Invalid Refresh Token"})
    assert client.post("/auth/refresh", json={"refresh_token": "old"}).status_code == 401


def test_refresh_rate_limit_stays_429(client, gotrue_replies):
    gotrue_replies.reply = (429, {"error_code": "over_request_rate_limit", "msg": "slow down"})
    assert client.post("/auth/refresh", json={"refresh_token": "rt"}).status_code == 429


@pytest.mark.parametrize("reply", [(502, None), (200, httpx.ConnectError("down"))])
def test_gotrue_down_is_503(client, gotrue_replies, reply):
    gotrue_replies.reply = reply
    assert client.post("/auth/login", json=CREDS).status_code == 503


def test_gotrue_unconfigured_is_503(client, gotrue_replies, monkeypatch):
    monkeypatch.delenv("SUPABASE_ANON_KEY")
    settings.cache_clear()
    assert client.post("/auth/login", json=CREDS).status_code == 503
    assert gotrue_replies.requests == []


def test_logout_revokes_with_the_callers_token(client, gotrue_replies):
    gotrue_replies.reply = (204, None)
    token = make_token()
    r = client.post("/auth/logout", json={"scope": "global"}, headers=bearer(token))
    assert r.status_code == 204
    sent = gotrue_replies.requests[0]
    assert str(sent.url) == "https://test.supabase.co/auth/v1/logout?scope=global"
    assert sent.headers["authorization"] == f"Bearer {token}"


def test_logout_already_revoked_is_still_204(client, gotrue_replies):
    gotrue_replies.reply = (403, {"error_code": "session_not_found", "msg": "gone"})
    assert client.post("/auth/logout", json={}, headers=bearer(make_token())).status_code == 204


def test_logout_needs_a_token(client, gotrue_replies):
    assert client.post("/auth/logout", json={}).status_code == 401
    assert gotrue_replies.requests == []


# --- every other route needs a token (main.py's app-wide require_user) --------

@pytest.fixture
def main_client(monkeypatch):
    os.environ.setdefault("NOVA_MOCK_LLM", "1")
    from app import main
    monkeypatch.setattr(main, "_API_KEY", "")  # the client-key check is test_api_key.py's job
    return TestClient(main.app)


@pytest.mark.parametrize("method,path", [
    ("GET", "/tools/gain"), ("GET", "/persona/graph"), ("GET", "/audit"),
    ("POST", "/event"), ("GET", "/notes"), ("GET", "/me"), ("POST", "/reminders/sync"),
])
def test_app_routes_need_a_token(main_client, method, path):
    r = main_client.request(method, path)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_app_routes_reject_a_bad_token(main_client):
    assert main_client.get("/tools/gain", headers=bearer(make_token(aud="anon"))).status_code == 401


def test_app_routes_accept_a_valid_token(main_client):
    assert main_client.get("/tools/gain", headers=bearer(make_token())).status_code == 200


@pytest.mark.parametrize("path", ["/auth/login", "/auth/signup", "/auth/refresh"])
def test_token_issuing_routes_stay_public(main_client, gotrue_replies, path):
    body = {"refresh_token": "rt"} if path == "/auth/refresh" else CREDS
    assert main_client.post(path, json=body).status_code == 200


def test_healthz_stays_public(main_client):
    assert main_client.get("/health").status_code == 200


def test_auth_disabled_opens_app_routes_for_local_dev(main_client, monkeypatch):
    monkeypatch.setenv("NOVA_AUTH_DISABLED", "1")
    settings.cache_clear()
    assert main_client.get("/tools/gain").status_code == 200
