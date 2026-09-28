"""
api/me.py - the signed-in user  (Riley, 5.7)

    GET   /me              -> {user_id, email, profile}   (Bearer)
    POST  /me/onboarding   {display_name?, answers}  -> profile
    PATCH /me/profile      {display_name?, answers?} -> profile

The phone calls GET /me on start-up to confirm its session is still good, and
caches the answer so an offline cold start doesn't bounce the user back to
onboarding. `profile` is null until the user first finishes (or skips through)
onboarding; the phone shows onboarding while it is null or its
onboarding_version is older than the phone's.

What saving does beyond storing the answers - Persona facts, starting gains -
is store/profile.py's.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from app.core.auth import AuthUser, current_user
from app.schemas.auth import MeOut
from app.schemas.profile import OnboardingIn, ProfileOut, ProfilePatch
from app.store import profile

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
