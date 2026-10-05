"""
api/me.py - the signed-in user  (Riley, 5.7)

    GET   /me              -> {user_id, email, profile}   (Bearer)
    POST  /me/onboarding   {display_name?, answers}  -> profile
    PATCH /me/profile      {display_name?, answers?} -> profile
    POST  /me/delete       {password}                -> 204

The phone calls GET /me on start-up to confirm its session is still good, and
caches the answer so an offline cold start doesn't bounce the user back to
onboarding. `profile` is null until the user first finishes (or skips through)
onboarding; the phone shows onboarding while it is null or its
onboarding_version is older than the phone's.

What saving does beyond storing the answers - Persona facts, starting gains -
is store/profile.py's.

/me/delete deletes the account and everything in it, for good
(store/account.py). The password is checked against GoTrue first: 403
wrong_password if it's wrong, GoTrue's 429 passed through. 503 if the delete
couldn't finish - the phone keeps the user signed in to try again.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError

from app.core import gotrue
from app.core.auth import AuthUser, current_user
from app.core.config import settings
from app.schemas.auth import DeleteAccountIn, MeOut
from app.schemas.profile import OnboardingIn, ProfileOut, ProfilePatch
from app.store import account, profile

router = APIRouter(prefix="/me", tags=["me"])


def _unavailable(e: Exception) -> HTTPException:
    print(f"[profile] store unavailable: {e}")
    return HTTPException(status_code=503, detail="profile storage unavailable")


@router.get("", response_model=MeOut)
def me(user: AuthUser = Depends(current_user)) -> MeOut:
    try:
        saved = profile.get(user.id)
    except Exception as e:
        raise _unavailable(e)
    return MeOut(user_id=user.id, email=user.email, profile=saved)


@router.post("/onboarding", response_model=ProfileOut)
def complete_onboarding(body: OnboardingIn, user: AuthUser = Depends(current_user)) -> ProfileOut:
    try:
        return profile.complete_onboarding(user.id, body)
    except Exception as e:
        raise _unavailable(e)


@router.patch("/profile", response_model=ProfileOut)
def update_profile(patch: ProfilePatch, user: AuthUser = Depends(current_user)) -> ProfileOut:
    try:
        return profile.update(user.id, patch)
    except ValidationError as e:
        # A merged answer that isn't a valid OnboardingAnswers (a misspelt day, say).
        raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_input=False))
    except Exception as e:
        raise _unavailable(e)


@router.post("/delete", status_code=204)
def delete_account(body: DeleteAccountIn, user: AuthUser = Depends(current_user)) -> Response:
    if settings().auth_disabled or not user.email:
        # The local-dev user has no password and no Auth account to delete.
        raise HTTPException(status_code=400, detail="this account can't be deleted from here")
    try:
        gotrue.login(user.email, body.password)
    except gotrue.GoTrueError as e:
        if e.status == 429:
            raise HTTPException(status_code=429, detail={"code": e.code, "message": e.message})
        # A retry after a delete whose answer never reached the phone: the
        # password can't match an account that's gone, but its token still
        # verifies until it expires. Finish (or repeat) the delete instead.
        if _already_deleted(user.id):
            account.delete(user.id)
            return Response(status_code=204)
        raise HTTPException(status_code=403, detail={"code": "wrong_password", "message": "Wrong password."})
    except gotrue.GoTrueUnavailable:
        raise HTTPException(status_code=503, detail="sign-in service unavailable")
    try:
        account.delete(user.id)
    except Exception as e:
        print(f"[account] delete of {user.id} failed: {e}")
        raise HTTPException(status_code=503, detail="couldn't delete the account right now")
    return Response(status_code=204)


def _already_deleted(user_id) -> bool:
    try:
        return not gotrue.user_exists(str(user_id))
    except (gotrue.GoTrueError, gotrue.GoTrueUnavailable):
        return False
