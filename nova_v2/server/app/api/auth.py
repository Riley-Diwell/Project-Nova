"""
api/auth.py - sign-up, sign-in, refresh, sign-out  (Riley, 5.7)

    POST /auth/signup    {email, password}  -> Session, or {confirmation_required: true}
    POST /auth/login     {email, password}  -> Session
    POST /auth/refresh   {refresh_token}    -> Session
    POST /auth/logout    {scope}            -> 204   (Bearer)

Thin pass-throughs to GoTrue (core/gotrue.py), so the phone only ever talks to
this server. Like every route, these still need the X-Nova-Api-Key client key
(main.py), which keeps drive-by sign-ups out.

Errors:
  - GoTrue's own refusals pass through with its status and
    detail {code, message} - e.g. 400 invalid_credentials, 422 weak_password,
    429 over_request_rate_limit.
  - A refresh GoTrue refuses is always 401, the phone's cue to sign out.
  - GoTrue unreachable or unconfigured is 503, so the phone retries rather than
    treating the user as signed out.

Handlers are plain `def`: the GoTrue calls block, so FastAPI runs them in its
threadpool rather than stalling the event loop.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core import gotrue
from app.core.auth import AuthUser, bearer_token, current_user
from app.schemas.auth import ConfirmationRequired, Credentials, LogoutIn, RefreshIn, Session

router = APIRouter(prefix="/auth", tags=["auth"])


def _refused(e: gotrue.GoTrueError, status: int | None = None) -> HTTPException:
    return HTTPException(status_code=status or e.status, detail={"code": e.code, "message": e.message})


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="sign-in service unavailable")


@router.post("/signup", response_model=Session | ConfirmationRequired)
def signup(body: Credentials) -> Session | ConfirmationRequired:
    try:
        result = gotrue.signup(body.email, body.password)
    except gotrue.GoTrueError as e:
        raise _refused(e)
    except gotrue.GoTrueUnavailable:
        raise _unavailable()
    if "access_token" not in result:
        return ConfirmationRequired()
    return Session.from_gotrue(result)


@router.post("/login", response_model=Session)
def login(body: Credentials) -> Session:
    try:
        return Session.from_gotrue(gotrue.login(body.email, body.password))
    except gotrue.GoTrueError as e:
        raise _refused(e)
    except gotrue.GoTrueUnavailable:
        raise _unavailable()


@router.post("/refresh", response_model=Session)
def refresh(body: RefreshIn) -> Session:
    try:
        return Session.from_gotrue(gotrue.refresh(body.refresh_token))
    except gotrue.GoTrueError as e:
        # Rate limits are worth retrying; any other refusal means the refresh
        # token is dead (revoked, reused or expired) and the user must sign in.
        raise _refused(e, None if e.status == 429 else 401)
    except gotrue.GoTrueUnavailable:
        raise _unavailable()


@router.post("/logout", status_code=204)
def logout(
    body: LogoutIn,
    _user: AuthUser = Depends(current_user),
    token: str | None = Depends(bearer_token),
) -> Response:
    if token:  # None only with NOVA_AUTH_DISABLED, where there's nothing to revoke
        try:
            gotrue.logout(token, body.scope)
        except gotrue.GoTrueError as e:
            # Already signed out on GoTrue's side is still signed out.
            if e.status not in (401, 403, 404):
                raise _refused(e)
        except gotrue.GoTrueUnavailable:
            raise _unavailable()
    return Response(status_code=204)
