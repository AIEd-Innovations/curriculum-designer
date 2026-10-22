"""A teacher can generate with their own API key (X-LLM-API-Key + X-LLM-Model).

Real app and lifespan, mock server-side backend, in-memory stores. The vendor
call is faked, so these run with no network and no credentials.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from models.curriculum import LabMaterial, Rubric
from services.llm import ChatLLMClient, LLMAuthError, LLMError

KEY = "sk-teacher-secret-123"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "memory")
    monkeypatch.setenv("LLM_BACKEND", "mock")

    import config

    importlib.reload(config)

    import main

    importlib.reload(main)
    return main.app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        now = datetime.now(timezone.utc)
        app.state.store.put(
            LabMaterial(
                lab_id="lab4",
                course_id="csc580",
                title="Linked Lists",
                spec_markdown="# Lab 4",
                quiz=[],
                rubric=Rubric(),
                learning_objectives=["Understand pointers"],
                generated_at=now,
                last_updated=now,
            )
        )
        yield c


class FakeClient:
    """Stands in for a per-key client. Records that it was used."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = 0

    async def check_typos(self, **_kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return []


def use_fake(monkeypatch, fake: FakeClient) -> list[tuple[str, str]]:
    built: list[tuple[str, str]] = []

    def build(model_id: str, api_key: str):
        built.append((model_id, api_key))
        return fake

    monkeypatch.setattr("routers.curriculum.build_client_for_key", build)
    return built


def test_models_endpoint_lists_the_supported_models(client):
    models = client.get("/curriculum/models").json()["models"]
    ids = {m["id"] for m in models}
    assert {"claude-sonnet-5-5", "gpt-6-astra", "gemini-3.8-flash"} <= ids
    assert {m["vendor"] for m in models} == {"anthropic", "openai", "gemini"}


def test_without_a_key_the_server_default_is_used(client, monkeypatch):
    built = use_fake(monkeypatch, FakeClient())
    r = client.post("/curriculum/lab4/check-typos")
    assert r.status_code == 200
    assert built == []  # mock server client answered, nothing built per key


def test_key_and_model_build_a_client_for_that_request(client, monkeypatch):
    fake = FakeClient()
    built = use_fake(monkeypatch, fake)
    r = client.post(
        "/curriculum/lab4/check-typos",
        headers={"X-LLM-API-Key": KEY, "X-LLM-Model": "gpt-6-astra"},
    )
    assert r.status_code == 200
    assert built == [("gpt-6-astra", KEY)]
    assert fake.calls == 1


def test_key_without_a_model_is_rejected(client):
    r = client.post("/curriculum/lab4/check-typos", headers={"X-LLM-API-Key": KEY})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MODEL_REQUIRED"


def test_unknown_model_is_rejected(client):
    r = client.post(
        "/curriculum/lab4/check-typos",
        headers={"X-LLM-API-Key": KEY, "X-LLM-Model": "not-a-model"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "UNSUPPORTED_MODEL"
    assert KEY not in r.text


def test_a_key_the_vendor_rejects_returns_401_without_echoing_it(client, monkeypatch):
    use_fake(monkeypatch, FakeClient(error=LLMAuthError(f"bad key {KEY}")))
    r = client.post(
        "/curriculum/lab4/check-typos",
        headers={"X-LLM-API-Key": KEY, "X-LLM-Model": "claude-sonnet-5-5"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "LLM_KEY_REJECTED"
    assert KEY not in r.text


def test_other_model_failures_return_502(client, monkeypatch):
    use_fake(monkeypatch, FakeClient(error=LLMError("upstream timeout")))
    r = client.post(
        "/curriculum/lab4/check-typos",
        headers={"X-LLM-API-Key": KEY, "X-LLM-Model": "claude-sonnet-5-5"},
    )
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "LLM_ERROR"


# ── ChatLLMClient ─────────────────────────────────────────────────────────────

class FailingChat:
    def __init__(self, exc: Exception):
        self.exc = exc

    async def ainvoke(self, _messages):
        raise self.exc


class AuthError(Exception):
    status_code = 401


def test_error_text_never_contains_the_key():
    chat = FailingChat(RuntimeError(f"upstream said no for {KEY}"))
    client = ChatLLMClient(chat, secret=KEY)
    with pytest.raises(LLMError) as err:
        asyncio.run(client._invoke("sys", "user"))
    assert KEY not in str(err.value)
    assert "[redacted]" in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__  # no key in the chain


def test_a_401_from_the_vendor_becomes_llm_auth_error():
    client = ChatLLMClient(FailingChat(AuthError(f"Incorrect API key {KEY}")), secret=KEY)
    with pytest.raises(LLMAuthError) as err:
        asyncio.run(client._invoke("sys", "user"))
    assert KEY not in str(err.value)


# ── build_client_for_key ──────────────────────────────────────────────────────

def test_openai_models_build_a_real_client():
    from services.llm_models import build_client_for_key

    client = build_client_for_key("gpt-6-astra", KEY)
    assert isinstance(client, ChatLLMClient)


def test_missing_vendor_library_is_a_clear_error(monkeypatch):
    from services.llm_models import build_client_for_key

    monkeypatch.setitem(sys.modules, "langchain_anthropic", None)
    with pytest.raises(LLMError, match="anthropic client library is not installed"):
        build_client_for_key("claude-sonnet-5-5", KEY)
