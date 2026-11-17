"""Checks that a request comes from a signed-in Cal Poly instructor.

The instructor UI signs in with Microsoft and sends the ID token as
`Authorization: Bearer <token>`. It is verified against Microsoft's published
signing keys: signature, audience (our app's client ID), issuer and tenant
(Cal Poly's), expiry, and the email domain.

Ported from `assessment_agent/assessment_agent/auth.py` so the two services
accept exactly the same tokens; the settings come from `config.settings` here.

Off while AUTH_MS_CLIENT_ID is unset, so local development and the tests need
no sign-in: `require_instructor` returns None and `owned_course` waves every
request through.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import jwt
from fastapi import Depends, Header, HTTPException, Request

from config import settings


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Instructor:
    email: str
    name: str
    oid: str  # Microsoft's stable ID for the user (users.sso_subject in the shared database)


#: Who the caller is while sign-in is switched off (local dev and tests).
DEV_INSTRUCTOR = Instructor(email="dev@calpoly.edu", name="Local Dev", oid="local-dev")


def auth_enabled() -> bool:
    return bool(settings.auth_ms_client_id)


_key_clients: dict[str, jwt.PyJWKClient] = {}


def _signing_key(token: str, tenant: str):
    """Microsoft's public key for this token, fetched from the tenant's key set and cached."""
    client = _key_clients.setdefault(
        tenant,
        jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
            cache_keys=True,
        ),
    )
    return client.get_signing_key_from_jwt(token).key


def verify_id_token(token: str) -> Instructor:
    tenant = settings.auth_ms_tenant_id
    try:
        claims = jwt.decode(
            token,
            _signing_key(token, tenant),
            algorithms=["RS256"],
            audience=settings.auth_ms_client_id,
            issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
            options={"require": ["exp", "iat", "aud", "iss"]},
            leeway=60,
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"Invalid sign-in token: {exc}") from exc
    if claims.get("tid") != tenant:
        raise AuthError("This account is not from the allowed Microsoft directory")

    email = str(claims.get("preferred_username") or claims.get("email") or "").strip().lower()
    domain = settings.auth_allowed_email_domain
    local, _, host = email.rpartition("@")
    if not local or host != domain:
        raise AuthError(f"Only @{domain} accounts can use this service")
    return Instructor(
        email=email,
        name=str(claims.get("name") or email),
        oid=str(claims.get("oid") or ""),
    )


def require_instructor(authorization: str | None = Header(None)) -> Instructor | None:
    """FastAPI dependency. None when sign-in is not configured; otherwise the verified instructor, or 401."""
    if not auth_enabled():
        return None
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Sign in required", headers={"WWW-Authenticate": "Bearer"})
    try:
        return verify_id_token(token.strip())
    except AuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"})


# ── The caller's row in `users` ───────────────────────────────────────────────

def current_user(
    request: Request,
    instructor: Instructor | None = Depends(require_instructor),
) -> dict:
    """The signed-in instructor's `users` row, created on first sign-in.

    Anyone who signs in through the instructor dashboard is an instructor
    (issue #16): the student UI has its own sign-in. With sign-in off this is
    the fixed `DEV_INSTRUCTOR`, so `/me` still works locally.
    """
    return upsert_instructor(request, instructor or DEV_INSTRUCTOR)


def upsert_instructor(request: Request, instructor: Instructor) -> dict:
    system = getattr(request.app.state, "system_store", None)
    if system is None:
        # No shared database wired up (bare test apps): the identity is enough.
        return {"id": instructor.oid, "email": instructor.email, "name": instructor.name,
                "role": "instructor"}

    existing = system.get_user_by_sso_subject(instructor.oid)
    if existing is not None:
        return existing

    user = {
        "id": str(uuid4()),
        "email": instructor.email,
        "name": instructor.name,
        "sso_subject": instructor.oid,
        "role": "instructor",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    system.insert_user(user)
    return system.get_user_by_sso_subject(instructor.oid) or user


# ── Course ownership ──────────────────────────────────────────────────────────

def _forbidden(course_id: str) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={
            "error": {
                "code": "NOT_YOUR_COURSE",
                "message": f"Course '{course_id}' belongs to another instructor",
                "agent": "curriculum-designer",
            }
        },
    )


def owned_course(request: Request, user: dict, course_id: str) -> dict | None:
    """The course, if this user owns it. 403 otherwise.

    A no-op while sign-in is off: there is no caller to check against.
    """
    if not auth_enabled():
        return None
    system = getattr(request.app.state, "system_store", None)
    if system is None:
        return None
    course = system.get_course(course_id)
    if course is None or course.get("instructor_id") != user.get("id"):
        # Not saying which of the two it is: another instructor's course is
        # none of this caller's business either way.
        raise _forbidden(course_id)
    return course
