"""
Curriculum Designer data models.

Fields marked with †extension† are not strictly required by the contract but are useful internally and surface as
optional fields on the wire.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── Quiz ──────────────────────────────────────────────────────────────────────

QuestionType = Literal["short_answer", "multiple_choice", "code", "essay"]
DifficultyTier = Literal["basic", "intermediate", "challenge"]


class QuizQuestion(BaseModel):
    id: str                                       # "q1", "q2", ...
    question: str
    type: QuestionType
    expected_answer: str
    rubric_points: int
    # †extension†  Tiered tasks per Framework doc.
    difficulty: DifficultyTier
    # †extension†  Which learning objective this question targets (string match).
    learning_objective_ref: str | None = None


# ── Rubric ────────────────────────────────────────────────────────────────────

class RubricCriterion(BaseModel):
    name: str
    weight: float
    description: str


class Rubric(BaseModel):
    code_weight: float = 0.6
    report_weight: float = 0.3
    manual_weight: float = 0.1
    criteria: list[RubricCriterion] = Field(default_factory=list)


# ── Approval / history ────────────────────────────────────────────────────────

ApprovalStatus = Literal["pending", "approved", "needs_changes"]

# The three steps of generation, in order. Each is approved by the instructor
# before the next one is generated.
Step = Literal["spec", "quiz", "rubric"]
STEPS: tuple[str, ...] = ("spec", "quiz", "rubric")


class FeedbackEntry(BaseModel):
    """One round of instructor `request-changes` feedback."""
    feedback: str
    requested_by: str
    requested_at: datetime
    resulting_version: int   # version produced AFTER applying this feedback
    # Which step the feedback was about; "all" for the whole-lab request-changes.
    step: Literal["spec", "quiz", "rubric", "all"] = "all"


# ── Lab material (canonical curriculum object) ────────────────────────────────

class LabMaterial(BaseModel):
    lab_id: str
    course_id: str = "csc580"
    title: str
    spec_markdown: str
    quiz: list[QuizQuestion]
    rubric: Rubric

    learning_objectives: list[str]
    difficulty: str = "intermediate"
    estimated_duration_min: int = 90
    material_weight: str = "balanced"

    # RAG context — stored after instructor upload, passed verbatim to LLM prompts
    material_content: str | None = None       # full extracted PDF text
    agent_instructions: str | None = None     # freeform instructor instructions

    # Approval workflow
    approval_status: ApprovalStatus = "pending"
    approved_by: str | None = None
    approval_notes: str | None = None
    # Step-by-step approval. `approval_status` is "approved" only when all three are.
    spec_status: ApprovalStatus = "pending"
    quiz_status: ApprovalStatus = "pending"
    rubric_status: ApprovalStatus = "pending"
    feedback_history: list[FeedbackEntry] = Field(default_factory=list)

    # Versioning — bumped on every regeneration triggered by request-changes
    version: int = 1
    generated_at: datetime
    last_updated: datetime


# ── Request models (API inputs) ───────────────────────────────────────────────

class GenerateRequest(BaseModel):
    course_id: str = "csc580"
    lab_id: str
    title: str
    learning_objectives: list[str]
    difficulty: str = "intermediate"
    estimated_duration_min: int = 90
    instructor_id: str
    material_weight: Literal["strict", "balanced", "expansive"] = "balanced"


class ApproveRequest(BaseModel):
    approved_by: str
    notes: str = ""


class StepRequest(BaseModel):
    """Generate (or regenerate) one step. `feedback` is the instructor's change request, if any."""
    requested_by: str
    feedback: str | None = None
    # Quiz step only. Off by default: it costs two more model calls (a review, then a retry if
    # the review finds something), and one self-check is what the pipeline has always done.
    self_review: bool = False


class RequestChangesRequest(BaseModel):
    feedback: str
    requested_by: str


# ── Response models ───────────────────────────────────────────────────────────

class TypoIssue(BaseModel):
    location: str    # e.g. "Q3", "spec.intro", "rubric.criteria[0]"
    type: Literal["typo", "inconsistency", "ambiguity", "factual"]
    suggestion: str


class CheckTyposResponse(BaseModel):
    issues_found: int
    issues: list[TypoIssue]


# ── Upload models ─────────────────────────────────────────────────────────────

class UploadInstructionsRequest(BaseModel):
    instructions: str


class UploadAckResponse(BaseModel):
    lab_id: str
    field: str          # "material_content" or "agent_instructions"
    chars_stored: int
    message: str
