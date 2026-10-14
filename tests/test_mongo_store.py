"""MongoDB-backed store tests, run against mongomock.

These cover the Mongo-specific code the in-memory tests cannot reach: document
round-tripping through BSON, the unique (course_id, lab_id) index, and the
queries behind the file endpoints. `pip install mongomock` to run them; they
skip otherwise.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

mongomock = pytest.importorskip("mongomock")

from models.curriculum import LabMaterial, QuizQuestion, Rubric, RubricCriterion
from models.files import LabFile, build_storage_key
from services.storage import MongoFileStore, MongoStore


@pytest.fixture
def client():
    return mongomock.MongoClient()


@pytest.fixture
def store(client):
    return MongoStore(uri="", db_name="aied_test", client=client)


@pytest.fixture
def files(client):
    return MongoFileStore(uri="", db_name="aied_test", client=client)


def make_lab(lab_id: str = "lab4", course_id: str = "csc580") -> LabMaterial:
    now = datetime.now(timezone.utc)
    return LabMaterial(
        lab_id=lab_id,
        course_id=course_id,
        title="Linked Lists",
        spec_markdown="# Lab 4",
        quiz=[
            QuizQuestion(
                id="q1",
                question="Time complexity of head insertion?",
                type="short_answer",
                expected_answer="O(1)",
                rubric_points=10,
                difficulty="basic",
            )
        ],
        rubric=Rubric(
            criteria=[
                RubricCriterion(name="Correctness", weight=0.5, description="Works")
            ]
        ),
        learning_objectives=["Understand pointers"],
        generated_at=now,
        last_updated=now,
    )


# ── Round-tripping ────────────────────────────────────────────────────────────

def test_nested_models_survive_the_round_trip(store):
    store.put(make_lab())
    got = store.get("lab4")

    assert got.quiz[0].expected_answer == "O(1)"
    assert got.rubric.criteria[0].name == "Correctness"
    assert got.rubric.code_weight == 0.6


def test_mongo_id_is_not_leaked_into_the_model(store):
    store.put(make_lab())
    assert "_id" not in store.get("lab4").model_dump()


def test_put_is_an_upsert_not_a_duplicate(store):
    store.put(make_lab())
    lab = make_lab()
    lab.title = "Linked Lists, revised"
    store.put(lab)

    assert len(store.list()) == 1
    assert store.get("lab4").title == "Linked Lists, revised"


def test_get_missing_lab_returns_none(store):
    assert store.get("nope") is None


# ── The compound key ──────────────────────────────────────────────────────────

def test_two_courses_can_each_have_lab4(store):
    store.put(make_lab(course_id="csc580"))
    store.put(make_lab(course_id="csc480"))

    assert len(store.list()) == 2
    assert store.get("lab4", "csc480").course_id == "csc480"


def test_list_filters_by_course(store):
    store.put(make_lab(lab_id="lab4", course_id="csc580"))
    store.put(make_lab(lab_id="lab5", course_id="csc580"))
    store.put(make_lab(lab_id="lab4", course_id="csc480"))

    assert len(store.list(course_id="csc580")) == 2


def test_delete_scoped_to_one_course(store):
    store.put(make_lab(course_id="csc580"))
    store.put(make_lab(course_id="csc480"))

    assert store.delete("lab4", "csc580") is True
    assert store.get("lab4", "csc580") is None
    assert store.get("lab4", "csc480") is not None


def test_unique_index_exists_on_course_and_lab(client, store):
    indexes = client["aied_test"]["labs"].index_information()
    unique = [
        name for name, spec in indexes.items()
        if spec.get("unique") and spec["key"] == [("course_id", 1), ("lab_id", 1)]
    ]
    assert unique, f"no unique (course_id, lab_id) index among {list(indexes)}"


# ── Files ─────────────────────────────────────────────────────────────────────

def make_file(file_id: str, filename: str, text: str, kind: str = "material") -> LabFile:
    return LabFile(
        file_id=file_id,
        lab_id="lab4",
        course_id="csc580",
        filename=filename,
        size_bytes=len(text),
        kind=kind,
        uploaded_at=datetime.now(timezone.utc),
        storage_key=build_storage_key("csc580", "lab4", file_id, filename),
        extracted_text=text,
        char_count=len(text),
    )


def test_files_round_trip_and_list_in_upload_order(files):
    files.add(make_file("f1", "a.pdf", "alpha"))
    files.add(make_file("f2", "b.pdf", "beta"))

    assert [f.filename for f in files.list_for_lab("lab4")] == ["a.pdf", "b.pdf"]
    assert files.get("f1").extracted_text == "alpha"


def test_merged_text_from_mongo(files):
    files.add(make_file("f1", "a.pdf", "alpha"))
    files.add(make_file("f2", "notes.txt", "be strict", kind="instructions"))

    merged = files.merged_text("lab4")
    assert "alpha" in merged
    assert "be strict" not in merged


def test_delete_file(files):
    files.add(make_file("f1", "a.pdf", "alpha"))
    assert files.delete("f1") is True
    assert files.get("f1") is None
    assert files.delete("f1") is False
