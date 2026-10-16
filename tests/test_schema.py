"""Schema tests.

The important ones here compare the `$jsonSchema` validators against the
Pydantic models field by field. If someone adds a field to a model and forgets
services/schema.py, these fail — which is the whole point of keeping the
validators in the repo rather than clicking them into Atlas.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models.curriculum import FeedbackEntry, LabMaterial, QuizQuestion, Rubric, RubricCriterion
from models.files import LabFile
from services import schema


def model_fields(model) -> set[str]:
    return set(model.model_fields)


def schema_fields(json_schema: dict) -> set[str]:
    return set(json_schema["properties"]) - {"_id"}


# ── The models and the validators must agree ─────────────────────────────────

@pytest.mark.parametrize(
    "model, json_schema, label",
    [
        (LabMaterial, schema.LAB_SCHEMA, "labs"),
        (LabFile, schema.LAB_FILE_SCHEMA, "lab_files"),
        (QuizQuestion, schema.QUIZ_QUESTION_SCHEMA, "quiz question"),
        (Rubric, schema.RUBRIC_SCHEMA, "rubric"),
        (RubricCriterion, schema.RUBRIC_CRITERION_SCHEMA, "rubric criterion"),
        (FeedbackEntry, schema.FEEDBACK_ENTRY_SCHEMA, "feedback entry"),
    ],
)
def test_validator_covers_exactly_the_model_fields(model, json_schema, label):
    missing = model_fields(model) - schema_fields(json_schema)
    extra = schema_fields(json_schema) - model_fields(model)

    assert not missing, f"{label}: in the model but not the validator: {sorted(missing)}"
    assert not extra, f"{label}: in the validator but not the model: {sorted(extra)}"


@pytest.mark.parametrize(
    "model, json_schema, label",
    [
        (LabMaterial, schema.LAB_SCHEMA, "labs"),
        (LabFile, schema.LAB_FILE_SCHEMA, "lab_files"),
        (QuizQuestion, schema.QUIZ_QUESTION_SCHEMA, "quiz question"),
    ],
)
def test_required_fields_are_the_ones_without_defaults(model, json_schema, label):
    """A field with no default must be present in every document; one with a
    default may be omitted. Optional-but-defaulted fields are allowed either
    way, so this only checks the no-default ones are required."""
    no_default = {
        name for name, field in model.model_fields.items() if field.is_required()
    }
    not_required = no_default - set(json_schema["required"])
    assert not not_required, f"{label}: mandatory but not required by the validator: {sorted(not_required)}"


def test_enums_match_the_literal_types():
    assert schema.QUIZ_QUESTION_SCHEMA["properties"]["type"]["enum"] == schema.QUESTION_TYPES
    assert schema.LAB_SCHEMA["properties"]["approval_status"]["enum"] == schema.APPROVAL_STATUSES
    assert schema.LAB_FILE_SCHEMA["properties"]["kind"]["enum"] == schema.FILE_KINDS


def test_nullable_fields_accept_null():
    for field in ("material_content", "agent_instructions", "approved_by", "approval_notes"):
        assert "null" in schema.LAB_SCHEMA["properties"][field]["bsonType"], field
    assert "null" in schema.LAB_FILE_SCHEMA["properties"]["uploaded_by"]["bsonType"]


def test_every_collection_has_a_unique_index():
    for name, spec in schema.COLLECTIONS.items():
        assert any(unique for _keys, unique, _why in spec["indexes"]), f"{name} has no unique index"


# ── A real document from the models must satisfy the validator ───────────────

def make_lab() -> LabMaterial:
    now = datetime.now(timezone.utc)
    return LabMaterial(
        lab_id="lab4",
        title="Linked Lists",
        spec_markdown="# Lab 4",
        quiz=[
            QuizQuestion(
                id="q1",
                question="Cost of head insertion?",
                type="short_answer",
                expected_answer="O(1)",
                rubric_points=10,
                difficulty="basic",
            )
        ],
        rubric=Rubric(criteria=[RubricCriterion(name="Correctness", weight=0.5, description="d")]),
        learning_objectives=["Understand pointers"],
        generated_at=now,
        last_updated=now,
    )


def test_a_dumped_lab_has_no_field_the_validator_would_reject():
    """additionalProperties is false, so anything model_dump emits must be
    declared — this is the check that catches that."""
    document = make_lab().model_dump(mode="python")
    undeclared = set(document) - set(schema.LAB_SCHEMA["properties"])
    assert not undeclared, f"model_dump emits undeclared fields: {sorted(undeclared)}"


def test_a_dumped_lab_contains_every_required_field():
    document = make_lab().model_dump(mode="python")
    missing = set(schema.LAB_SCHEMA["required"]) - set(document)
    assert not missing, f"required by the validator but absent: {sorted(missing)}"


def test_a_dumped_quiz_question_matches_the_embedded_schema():
    document = make_lab().model_dump(mode="python")["quiz"][0]
    assert set(document) <= set(schema.QUIZ_QUESTION_SCHEMA["properties"])
    assert set(schema.QUIZ_QUESTION_SCHEMA["required"]) <= set(document)


# ── Applying the schema ──────────────────────────────────────────────────────

def test_validator_for_wraps_in_jsonschema():
    assert schema.validator_for("labs") == {"$jsonSchema": schema.LAB_SCHEMA}


class FakeCollection:
    def __init__(self):
        self.indexes: list[tuple] = []

    def create_index(self, keys, unique=False):
        self.indexes.append((keys, unique))


class FakeDB:
    """Records what ensure_collection would do. mongomock cannot create a
    collection with validator options, and enforcement itself is MongoDB's
    job — what is worth testing here is that we create once and collMod
    thereafter, and that the indexes follow."""

    def __init__(self, existing: list[str] | None = None):
        self.existing = list(existing or [])
        self.created: list[tuple[str, dict]] = []
        self.commands: list[dict] = []
        self.collections: dict[str, FakeCollection] = {}

    def list_collection_names(self):
        return list(self.existing)

    def create_collection(self, name, **kwargs):
        self.created.append((name, kwargs))
        self.existing.append(name)

    def command(self, body):
        self.commands.append(body)

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


def test_ensure_all_creates_collections_with_their_validators():
    db = FakeDB()
    result = schema.ensure_all(db)

    assert set(result) == set(schema.COLLECTIONS)
    assert set(result.values()) == {"created"}

    created = dict(db.created)
    assert set(created) == set(schema.COLLECTIONS)
    assert created["labs"]["validator"] == {"$jsonSchema": schema.LAB_SCHEMA}
    assert created["labs"]["validationLevel"] == "moderate"


def test_ensure_all_is_idempotent_and_uses_collmod_when_present():
    db = FakeDB(existing=list(schema.COLLECTIONS))
    result = schema.ensure_all(db)

    assert set(result.values()) == {"updated"}
    assert db.created == []
    assert [c["collMod"] for c in db.commands] == list(schema.COLLECTIONS)
    assert db.commands[0]["validator"] == {"$jsonSchema": schema.LAB_SCHEMA}


def test_ensure_all_creates_every_declared_index():
    db = FakeDB()
    schema.ensure_all(db)

    for name, spec in schema.COLLECTIONS.items():
        expected = [(keys, unique) for keys, unique, _why in spec["indexes"]]
        assert db[name].indexes == expected, name
