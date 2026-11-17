"""Sign-in, instructors, courses and course isolation — issue #16.

The tokens here are forged with a local RSA key and `auth._signing_key` is
replaced, so nothing talks to Microsoft. The app runs with the memory stores,
so nothing talks to MongoDB either.
"""

from __future__ import annotations

import importlib
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

CLIENT_ID = "11111111-2222-3333-4444-555555555555"
TENANT = "1b0d02db-fc9e-4495-9537-1d379cca2ae7"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

ALICE = ("alice@calpoly.edu", "Alice Alvarez", "oid-alice")
BOB = ("bob@calpoly.edu", "Bob Brown", "oid-bob")


def token(who=ALICE, key=KEY, **overrides) -> str:
    email, name, oid = who
    now = int(time.time())
    claims = {
        "aud": CLIENT_ID,
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "tid": TENANT,
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
        "preferred_username": email,
        "name": name,
        "oid": oid,
        **overrides,
    }
    return jwt.encode(
        {k: v for k, v in claims.items() if v is not None},
        key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )


def bearer(who=ALICE, **overrides) -> dict:
    return {"Authorization": f"Bearer {token(who, **overrides)}"}


def message(resp) -> str:
    """The message out of the contract's error envelope (main.py reshapes it)."""
    return resp.json()["error"]["message"]


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "memory")
    monkeypatch.setenv("LLM_BACKEND", "mock")

    import config

    importlib.reload(config)
    import auth
    import main

    importlib.reload(auth)
    importlib.reload(main)
    return main.app


@pytest.fixture
def signin_off(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def client(app, monkeypatch):
    """A client with sign-in switched on and Microsoft's key set stubbed out."""
    import auth
    from config import settings

    monkeypatch.setattr(settings, "auth_ms_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "auth_ms_tenant_id", TENANT)
    monkeypatch.setattr(settings, "auth_allowed_email_domain", "calpoly.edu")
    monkeypatch.setattr(auth, "_signing_key", lambda _token, _tenant: KEY.public_key())
    with TestClient(app) as client:
        yield client


# ── The token ─────────────────────────────────────────────────────────────────

def test_everything_is_open_while_signin_is_not_configured(signin_off):
    """Local dev and the other tests: no AUTH_MS_CLIENT_ID, no sign-in."""
    assert signin_off.get("/curriculum/models").status_code == 200
    assert signin_off.get("/me").status_code == 200


def test_health_and_root_stay_open(client):
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200


def test_no_token_gives_401(client):
    for path in ("/me", "/curriculum/models", "/curriculum/lab1", "/courses/c1/labs"):
        resp = client.get(path)
        assert resp.status_code == 401, path
        assert resp.headers["www-authenticate"] == "Bearer"
    assert client.post("/courses", json={"name": "CSC 580"}).status_code == 401
    assert client.get("/me", headers={"Authorization": "Basic abc"}).status_code == 401


@pytest.mark.parametrize("overrides, reason", [
    ({"aud": "someone-elses-app"}, "Audience"),
    ({"iss": "https://login.microsoftonline.com/other/v2.0"}, "issuer"),
    ({"exp": int(time.time()) - 3600}, "expired"),
    ({"tid": "other-tenant"}, "directory"),
    ({"preferred_username": "someone@gmail.com"}, "@calpoly.edu"),
    ({"preferred_username": "x@calpoly.edu.evil.com"}, "@calpoly.edu"),
])
def test_bad_tokens_are_refused_with_a_reason(client, overrides, reason):
    resp = client.get("/me", headers=bearer(**overrides))
    assert resp.status_code == 401
    assert reason in message(resp)


def test_a_token_signed_by_someone_else_is_refused(client):
    headers = {"Authorization": f"Bearer {token(key=OTHER_KEY)}"}
    assert client.get("/me", headers=headers).status_code == 401


def test_junk_instead_of_a_token_is_refused(client):
    resp = client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
    assert "Invalid sign-in token" in message(resp)


# ── /me and /courses ──────────────────────────────────────────────────────────

def test_a_new_person_becomes_an_instructor_with_no_courses(client):
    body = client.get("/me", headers=bearer(ALICE)).json()
    assert body["user"]["email"] == "alice@calpoly.edu"
    assert body["user"]["name"] == "Alice Alvarez"
    assert body["courses"] == []

    # The row is created once, not on every call.
    again = client.get("/me", headers=bearer(ALICE)).json()
    assert again["user"]["id"] == body["user"]["id"]


def test_a_course_shows_up_in_its_owners_me_only(client):
    created = client.post("/courses", json={"name": "CSC 580"}, headers=bearer(ALICE))
    assert created.status_code == 201
    course = created.json()
    assert course["name"] == "CSC 580"

    mine = client.get("/me", headers=bearer(ALICE)).json()
    assert mine["courses"] == [{"id": course["id"], "name": "CSC 580"}]

    theirs = client.get("/me", headers=bearer(BOB)).json()
    assert theirs["courses"] == []


def test_a_course_needs_a_name(client):
    resp = client.post("/courses", json={"name": "   "}, headers=bearer(ALICE))
    assert resp.status_code == 422


# ── Only your own course ──────────────────────────────────────────────────────

def _lab(client, course_id: str, who, lab_id: str = "lab1") -> dict:
    resp = client.post(
        "/curriculum/generate-spec",
        json={
            "course_id": course_id,
            "lab_id": lab_id,
            "title": "Sorting",
            "learning_objectives": ["Compare sorts"],
            "instructor_id": "ignored-by-the-server",
        },
        headers=bearer(who),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_one_instructor_cannot_see_or_change_anothers_course_or_lab(client):
    course = client.post("/courses", json={"name": "CSC 580"}, headers=bearer(ALICE)).json()
    _lab(client, course["id"], ALICE)

    # Alice can read her own lab and list her course's labs.
    assert client.get(
        "/curriculum/lab1", params={"course_id": course["id"]}, headers=bearer(ALICE)
    ).status_code == 200
    labs = client.get(f"/courses/{course['id']}/labs", headers=bearer(ALICE)).json()
    assert [lab["lab_id"] for lab in labs["labs"]] == ["lab1"]

    # Bob gets 403 on the course and on everything inside it.
    assert client.get(f"/courses/{course['id']}/labs", headers=bearer(BOB)).status_code == 403
    for method, path in [
        ("get", "/curriculum/lab1"),
        ("get", "/curriculum/lab1/files"),
        ("get", "/curriculum/lab1/export/lab.pdf"),
    ]:
        resp = getattr(client, method)(
            path, params={"course_id": course["id"]}, headers=bearer(BOB)
        )
        assert resp.status_code == 403, path
    assert client.post(
        "/curriculum/lab1/approve",
        params={"course_id": course["id"]},
        json={"approved_by": "bob@calpoly.edu", "notes": ""},
        headers=bearer(BOB),
    ).status_code == 403
    assert client.post(
        "/curriculum/lab1/upload-instructions",
        params={"course_id": course["id"]},
        json={"instructions": "mine now"},
        headers=bearer(BOB),
    ).status_code == 403

    # And Bob cannot write a lab into her course either.
    denied = client.post(
        "/curriculum/generate-spec",
        json={
            "course_id": course["id"],
            "lab_id": "lab-bob",
            "title": "Hijack",
            "learning_objectives": ["None"],
            "instructor_id": "bob",
        },
        headers=bearer(BOB),
    )
    assert denied.status_code == 403


def test_each_instructor_only_lists_their_own_labs(client):
    a = client.post("/courses", json={"name": "CSC 580"}, headers=bearer(ALICE)).json()
    b = client.post("/courses", json={"name": "CSC 581"}, headers=bearer(BOB)).json()
    _lab(client, a["id"], ALICE, lab_id="alice-lab")
    _lab(client, b["id"], BOB, lab_id="bob-lab")

    assert [lab["lab_id"] for lab in
            client.get(f"/courses/{a['id']}/labs", headers=bearer(ALICE)).json()["labs"]] == ["alice-lab"]
    assert [lab["lab_id"] for lab in
            client.get(f"/courses/{b['id']}/labs", headers=bearer(BOB)).json()["labs"]] == ["bob-lab"]


def test_a_course_nobody_owns_is_refused(client):
    resp = client.get(
        "/curriculum/lab1", params={"course_id": "no-such-course"}, headers=bearer(ALICE)
    )
    assert resp.status_code == 403


# ── Who did it ────────────────────────────────────────────────────────────────

def test_the_signed_in_email_is_recorded_not_the_browsers_claim(client):
    course = client.post("/courses", json={"name": "CSC 580"}, headers=bearer(ALICE)).json()
    _lab(client, course["id"], ALICE)

    approved = client.post(
        "/curriculum/lab1/steps/spec/approve",
        params={"course_id": course["id"]},
        json={"approved_by": "someone.else@calpoly.edu", "notes": "ok"},
        headers=bearer(ALICE),
    )
    assert approved.status_code == 200

    changed = client.post(
        "/curriculum/lab1/steps/spec/generate",
        params={"course_id": course["id"]},
        json={"requested_by": "someone.else@calpoly.edu", "feedback": "more detail"},
        headers=bearer(ALICE),
    ).json()
    assert changed["feedback_history"][-1]["requested_by"] == "alice@calpoly.edu"

    for step in ("spec", "quiz", "rubric"):
        if step != "spec":
            client.post(
                f"/curriculum/lab1/steps/{step}/generate",
                params={"course_id": course["id"]},
                json={"requested_by": "x"},
                headers=bearer(ALICE),
            )
        client.post(
            f"/curriculum/lab1/steps/{step}/approve",
            params={"course_id": course["id"]},
            json={"approved_by": "someone.else@calpoly.edu", "notes": "ok"},
            headers=bearer(ALICE),
        )
    lab = client.get(
        "/curriculum/lab1", params={"course_id": course["id"]}, headers=bearer(ALICE)
    ).json()
    assert lab["approval_status"] == "approved"
    assert lab["approved_by"] == "alice@calpoly.edu"


def test_the_email_claim_is_used_when_there_is_no_preferred_username(client):
    import auth

    verified = auth.verify_id_token(
        token(preferred_username=None, email="Prof.Smith@CalPoly.edu")
    )
    assert verified.email == "prof.smith@calpoly.edu"
