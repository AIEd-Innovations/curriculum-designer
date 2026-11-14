"""Step-by-step generation: the instructor approves the spec, then the quiz, then the rubric."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

BODY = {
    "lab_id": "lab9",
    "title": "Trees",
    "learning_objectives": ["Traverse a tree"],
    "instructor_id": "prof",
}
WHO = {"requested_by": "prof"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "memory")
    monkeypatch.setenv("LLM_BACKEND", "mock")
    import config

    importlib.reload(config)
    import main

    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def start(client):
    r = client.post("/curriculum/generate-spec", json=BODY)
    assert r.status_code == 200, r.text
    return r.json()


def step(client, name, action, **body):
    payload = {"approved_by": "prof"} if action == "approve" else dict(WHO)
    payload.update(body)
    return client.post(f"/curriculum/lab9/steps/{name}/{action}", json=payload)


def test_generate_spec_creates_only_the_spec(client):
    lab = start(client)
    assert lab["spec_markdown"].startswith("# Lab: Trees")
    assert lab["quiz"] == [] and lab["rubric"]["criteria"] == []
    assert (lab["spec_status"], lab["quiz_status"], lab["rubric_status"]) == ("pending",) * 3


def test_quiz_is_locked_until_the_spec_is_approved(client):
    start(client)
    r = step(client, "quiz", "generate")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "STEP_NOT_READY"


def test_rubric_is_locked_until_the_quiz_is_approved(client):
    start(client)
    step(client, "spec", "approve")
    assert step(client, "rubric", "generate").status_code == 409


def test_cannot_approve_a_step_out_of_order_or_before_it_exists(client):
    start(client)
    assert step(client, "quiz", "approve").status_code == 409  # spec not approved
    step(client, "spec", "approve")
    assert step(client, "quiz", "approve").json()["error"]["code"] == "STEP_NOT_GENERATED"


def test_full_flow_ends_with_the_lab_approved(client):
    start(client)
    assert step(client, "spec", "approve").json()["approval_status"] == "pending"
    assert len(step(client, "quiz", "generate").json()["quiz"]) == 3
    step(client, "quiz", "approve")
    assert step(client, "rubric", "generate").json()["rubric"]["criteria"]
    lab = step(client, "rubric", "approve").json()
    assert lab["approval_status"] == "approved"
    assert lab["approved_by"] == "prof"
    assert lab["version"] == 1


def test_spec_feedback_regenerates_the_spec_and_relocks_later_steps(client):
    start(client)
    step(client, "spec", "approve")
    step(client, "quiz", "generate")
    step(client, "quiz", "approve")

    lab = step(client, "spec", "generate", feedback="add a recursion section").json()
    assert "add a recursion section" in lab["spec_markdown"]
    assert lab["spec_status"] == "needs_changes"
    assert lab["quiz_status"] == "pending"
    assert lab["version"] == 2
    assert lab["feedback_history"][-1]["step"] == "spec"
    # the quiz cannot be approved again until the new spec is
    assert step(client, "quiz", "approve").status_code == 409


def test_quiz_feedback_leaves_the_spec_alone(client):
    spec = start(client)["spec_markdown"]
    step(client, "spec", "approve")
    lab = step(client, "quiz", "generate", feedback="more code questions").json()
    assert lab["spec_markdown"] == spec
    assert lab["spec_status"] == "approved"
    assert lab["quiz_status"] == "needs_changes"
    assert all("more code questions" in q["question"] for q in lab["quiz"])


def test_unknown_step_and_lab(client):
    start(client)
    assert step(client, "essay", "generate").status_code == 404
    assert client.post("/curriculum/nope/steps/spec/generate", json=WHO).status_code == 404


def test_whole_lab_approve_marks_every_step_approved(client):
    client.post("/curriculum/generate", json=BODY)
    lab = client.post("/curriculum/lab9/approve", json={"approved_by": "prof"}).json()
    assert (lab["spec_status"], lab["quiz_status"], lab["rubric_status"]) == ("approved",) * 3


def test_whole_lab_request_changes_puts_quiz_and_rubric_back_to_pending(client):
    client.post("/curriculum/generate", json=BODY)
    client.post("/curriculum/lab9/approve", json={"approved_by": "prof"})
    lab = client.post("/curriculum/lab9/request-changes", json={"feedback": "x", "requested_by": "prof"}).json()
    assert lab["spec_status"] == "approved"
    assert (lab["quiz_status"], lab["rubric_status"]) == ("pending", "pending")
    assert lab["feedback_history"][-1]["step"] == "all"


# ── Quiz self-review ──────────────────────────────────────────────────────────

class _Recorder:
    """Wraps the mock model to count calls and make the review find (or not find) problems."""

    def __init__(self, inner, notes):
        self.inner, self.notes = inner, notes
        self.quiz_feedback, self.review_rubrics = [], []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def generate_quiz(self, *args, **kwargs):
        self.quiz_feedback.append(kwargs.get("feedback"))
        return await self.inner.generate_quiz(*args, **kwargs)

    async def self_review(self, spec_markdown, quiz, rubric, objectives):
        self.review_rubrics.append(rubric)
        return self.notes


def _quiz_with_review(client, monkeypatch, notes, **body):
    import routers.curriculum as router_module

    recorder = _Recorder(router_module._llm_for(type("R", (), {"headers": {}, "app": client.app})()), notes)
    monkeypatch.setattr(router_module, "_llm_for", lambda request: recorder)
    start(client)
    step(client, "spec", "approve")
    lab = step(client, "quiz", "generate", **body).json()
    return recorder, lab


def test_self_review_is_off_unless_asked_for(client, monkeypatch):
    recorder, _ = _quiz_with_review(client, monkeypatch, ["Q1 is vague"])
    assert recorder.review_rubrics == []
    assert recorder.quiz_feedback == [None]


def test_self_review_with_no_problems_keeps_the_first_quiz(client, monkeypatch):
    recorder, lab = _quiz_with_review(client, monkeypatch, [], self_review=True)
    assert len(recorder.review_rubrics) == 1
    assert recorder.quiz_feedback == [None]
    assert len(lab["quiz"]) == 3


def test_self_review_problems_are_fed_back_once(client, monkeypatch):
    recorder, lab = _quiz_with_review(client, monkeypatch, ["Q1 is vague"], self_review=True, feedback="harder")
    assert len(recorder.review_rubrics) == 1  # one review, never a loop
    assert recorder.quiz_feedback[0] == "harder"
    assert "Q1 is vague" in recorder.quiz_feedback[1] and "harder" in recorder.quiz_feedback[1]
    # the review never sees a rubric: it has not been written yet
    assert recorder.review_rubrics[0].criteria == []
    # only the instructor's own feedback is logged as feedback
    assert [e["feedback"] for e in lab["feedback_history"]] == ["harder"]


def test_review_prompt_skips_the_rubric_check_when_there_is_no_rubric():
    from services.prompts import build_self_review_prompt

    _, without = build_self_review_prompt("spec", "[]", None, ["o"])
    _, with_rubric = build_self_review_prompt("spec", "[]", "{}", ["o"])
    assert "not written yet" in without
    assert "not written yet" not in with_rubric
