"""
schemas/auth.py - accounts, over the wire  (Riley, 5.7)

The shapes /auth/* and /me speak (api/auth.py, api/me.py).

No request here carries a user id: the server only takes the user from a
verified token (core/auth.py).
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.profile import ProfileOut


class Credentials(BaseModel):
    # GoTrue does the real email check; this just stops obvious junk early.
    email: str = Field(..., min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    # 72 is bcrypt's limit, which GoTrue hashes with.
    password: str = Field(..., min_length=8, max_length=72)


class RefreshIn(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class LogoutIn(BaseModel):
    scope: Literal["local", "global"] = "local"


class SessionUser(BaseModel):
    id: UUID
    email: str | None = None


class Session(BaseModel):
    """What the phone stores in its TokenStore."""
    access_token: str
    refresh_token: str
    expires_at: int = Field(..., description="Unix seconds when access_token expires.")
    user: SessionUser

    @classmethod
    def from_gotrue(cls, body: dict[str, Any]) -> "Session":
        user = body["user"]
        return cls(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            expires_at=body["expires_at"],
            user=SessionUser(id=user["id"], email=user.get("email")),
        )


class ConfirmationRequired(BaseModel):
    """Sign-up succeeded but the account needs its email confirmed first."""
    confirmation_required: Literal[True] = True


class MeOut(BaseModel):
    user_id: UUID
    email: str | None
    # Null until the user first finishes onboarding (store/profile.py).
    profile: ProfileOut | None = None
