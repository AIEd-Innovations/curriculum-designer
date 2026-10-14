"""Storage-layer tests. No MongoDB required — these cover the logic that is
shared between backends, using the in-memory implementations."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models.curriculum import LabMaterial, Rubric
from models.files import LabFile, build_storage_key
from services.storage import (
    MemoryFileStore,
    MemoryStore,
    build_file_store,
    build_store,
)


def make_lab(lab_id: str = "lab4", course_id: str = "csc580") -> LabMaterial:
    now = datetime.now(timezone.utc)
    return LabMaterial(
        lab_id=lab_id,
        course_id=course_id,
        title="Linked Lists",
        spec_markdown="# Lab",
        quiz=[],
        rubric=Rubric(),
        learning_objectives=["Understand pointers"],
        generated_at=now,
        last_updated=now,
    )


def make_file(file_id: str, filename: str, text: str, lab_id: str = "lab4") -> LabFile:
    return LabFile(
        file_id=file_id,
        lab_id=lab_id,
        course_id="csc580",
        filename=filename,
        size_bytes=len(text),
        uploaded_at=datetime.now(timezone.utc),
        storage_key=build_storage_key("csc580", lab_id, file_id, filename),
        extracted_text=text,
        char_count=len(text),
    )


# ── Lab store ─────────────────────────────────────────────────────────────────

def test_put_and_get_roundtrip():
    store = MemoryStore()
    store.put(make_lab())
    assert store.get("lab4").title == "Linked Lists"


def test_same_lab_id_in_two_courses_does_not_collide():
    """The bug the migration fixes: lab_id alone is not unique."""
    store = MemoryStore()
    store.put(make_lab(lab_id="lab4", course_id="csc580"))
    store.put(make_lab(lab_id="lab4", course_id="csc480"))

    assert len(store.list()) == 2
    assert store.get("lab4", "csc480").course_id == "csc480"
    assert len(store.list(course_id="csc580")) == 1


def test_delete():
    store = MemoryStore()
    store.put(make_lab())
    assert store.delete("lab4") is True
    assert store.get("lab4") is None
    assert store.delete("lab4") is False


# ── File store ────────────────────────────────────────────────────────────────

def test_merged_text_matches_the_old_inline_format():
    """material_content must look exactly as it did when the router merged
    PDFs inline, so the generation prompts see no change."""
    files = MemoryFileStore()
    files.add(make_file("f1", "week4.pdf", "pointer basics"))
    files.add(make_file("f2", "slides.pdf", "traversal"))

    merged = files.merged_text("lab4")
    # NB: the separator is glued straight onto the previous section — there is
    # no newline before the dashes. Copied verbatim from the router's old
    # inline merge, so the prompts see a byte-identical string.
    expected = (
        "\n\n[Document 1: week4.pdf]\npointer basics"
        + "-" * 64 + "\n\n"
        + "[Document 2: slides.pdf]\ntraversal"
    )
    assert merged == expected


def test_merged_text_is_empty_with_no_files():
    assert MemoryFileStore().merged_text("lab4") == ""


def test_delete_file_shrinks_merged_text():
    files = MemoryFileStore()
    files.add(make_file("f1", "a.pdf", "alpha"))
    files.add(make_file("f2", "b.pdf", "beta"))

    assert files.delete("f2") is True
    merged = files.merged_text("lab4")
    assert "alpha" in merged and "beta" not in merged


def test_files_are_scoped_to_their_lab():
    files = MemoryFileStore()
    files.add(make_file("f1", "a.pdf", "alpha", lab_id="lab4"))
    files.add(make_file("f2", "b.pdf", "beta", lab_id="lab5"))

    assert [f.file_id for f in files.list_for_lab("lab4")] == ["f1"]


def test_instructions_files_are_not_merged_into_material_content():
    files = MemoryFileStore()
    files.add(make_file("f1", "a.pdf", "alpha"))
    note = make_file("f2", "notes.txt", "be strict")
    note.kind = "instructions"
    files.add(note)

    assert "be strict" not in files.merged_text("lab4")
    assert len(files.list_for_lab("lab4")) == 2


def test_storage_key_convention():
    assert build_storage_key("csc580", "lab4", "abc", "week4.pdf") == (
        "labs/csc580/lab4/abc/week4.pdf"
    )


# ── Factories ─────────────────────────────────────────────────────────────────

def test_mongo_backend_without_uri_is_a_clear_error():
    with pytest.raises(ValueError, match="MONGODB_URI"):
        build_store("mongo")
    with pytest.raises(ValueError, match="MONGODB_URI"):
        build_file_store("mongo")


def test_cosmos_backend_points_at_mongo():
    with pytest.raises(NotImplementedError, match="STORAGE_BACKEND=mongo"):
        build_store("cosmos")
