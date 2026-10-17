"""The mock model cannot rewrite a quiz, but a change request must be visibly received.

Without this, a refinement in mock mode only bumps the version and the page looks unchanged.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

from services.llm import MockLLMClient


def quiz(feedback):
    return asyncio.run(MockLLMClient().generate_quiz("spec", ["objective"], None, None, feedback=feedback))


def test_without_feedback_the_questions_are_unchanged():
    assert all("Mock revision" not in q.question for q in quiz(None))


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_feedback_is_treated_as_none(blank):
    assert all("Mock revision" not in q.question for q in quiz(blank))


def test_feedback_is_shown_in_every_question():
    questions = quiz("make these about integrals")
    assert len(questions) == 3
    assert all('make these about integrals' in q.question for q in questions)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "memory")
    monkeypatch.setenv("LLM_BACKEND", "mock")
    import config

    importlib.reload(config)
    import main

    importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client


def test_request_changes_shows_the_feedback_and_each_request_replaces_the_last(client):
    created = client.post("/curriculum/generate", json={
        "lab_id": "lab-x", "title": "Integrals", "learning_objectives": ["integrate"], "instructor_id": "i1",
    })
    assert created.status_code == 200
    assert all("Mock revision" not in q["question"] for q in created.json()["quiz"])

    first = client.post("/curriculum/lab-x/request-changes", json={"feedback": "harder please", "requested_by": "i1"})
    body = first.json()
    assert body["version"] == 2 and body["approval_status"] == "needs_changes"
    assert all("harder please" in q["question"] for q in body["quiz"])

    second = client.post("/curriculum/lab-x/request-changes", json={"feedback": "add trees", "requested_by": "i1"}).json()
    assert second["version"] == 3
    assert all("add trees" in q["question"] and "harder please" not in q["question"] for q in second["quiz"])
    assert [entry["feedback"] for entry in second["feedback_history"]] == ["harder please", "add trees"]
