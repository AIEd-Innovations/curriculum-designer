"""End-to-end tests for the upload / list / delete file endpoints.

A minimal app is assembled here with the in-memory stores, so these run with no
MongoDB and no LLM. PDF parsing is stubbed — pypdf is exercised by its own
library tests, and what we care about is routing, persistence and the way
`material_content` is rebuilt from its source files.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.curriculum as curriculum_router_module
from models.curriculum import LabMaterial, Rubric
from routers.curriculum import router
from services.storage import MemoryFileStore, MemoryStore


@pytest.fixture
def client(monkeypatch):
    # Stub PDF extraction: the "text" of a file is just its bytes decoded.
    monkeypatch.setattr(
        curriculum_router_module,
        "_extract_pdf_text_from_bytes",
        lambda data, filename: data.decode("utf-8"),
    )

    app = FastAPI()
    app.include_router(router)
    app.state.store = MemoryStore()
    app.state.file_store = MemoryFileStore()

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
    with TestClient(app) as c:
        yield c


def upload(client, filename: str, text: str):
    return client.post(
        "/curriculum/lab4/upload-material",
        files={"file": (filename, text.encode("utf-8"), "application/pdf")},
    )


def test_upload_records_file_metadata(client):
    res = upload(client, "week4.pdf", "pointer basics")
    assert res.status_code == 200
    assert res.json()["chars_stored"] == len("\n\n[Document 1: week4.pdf]\npointer basics")

    listing = client.get("/curriculum/lab4/files").json()
    assert listing["lab_id"] == "lab4"
    assert len(listing["files"]) == 1

    file = listing["files"][0]
    assert file["filename"] == "week4.pdf"
    assert file["size_bytes"] == len("pointer basics")
    assert file["kind"] == "material"
    assert file["char_count"] == len("pointer basics")


def test_upload_populates_material_content(client):
    upload(client, "week4.pdf", "pointer basics")
    lab = client.get("/curriculum/lab4").json()
    assert "pointer basics" in lab["material_content"]


def test_two_uploads_both_appear(client):
    upload(client, "a.pdf", "alpha")
    upload(client, "b.pdf", "beta")

    listing = client.get("/curriculum/lab4/files").json()
    assert [f["filename"] for f in listing["files"]] == ["a.pdf", "b.pdf"]

    lab = client.get("/curriculum/lab4").json()
    assert "alpha" in lab["material_content"]
    assert "beta" in lab["material_content"]


def test_delete_removes_file_and_rebuilds_material_content(client):
    upload(client, "a.pdf", "alpha")
    upload(client, "b.pdf", "beta")
    file_id = client.get("/curriculum/lab4/files").json()["files"][1]["file_id"]

    res = client.delete(f"/curriculum/lab4/files/{file_id}")
    assert res.status_code == 200
    assert res.json()["deleted"] is True

    lab = client.get("/curriculum/lab4").json()
    assert "alpha" in lab["material_content"]
    assert "beta" not in lab["material_content"]


def test_deleting_the_last_file_clears_material_content(client):
    upload(client, "a.pdf", "alpha")
    file_id = client.get("/curriculum/lab4/files").json()["files"][0]["file_id"]
    client.delete(f"/curriculum/lab4/files/{file_id}")

    assert client.get("/curriculum/lab4").json()["material_content"] is None


def test_delete_unknown_file_is_404(client):
    res = client.delete("/curriculum/lab4/files/does-not-exist")
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "FILE_NOT_FOUND"


def test_listing_files_for_unknown_lab_is_404(client):
    assert client.get("/curriculum/nope/files").status_code == 404


def test_upload_with_no_file_is_422(client):
    res = client.post("/curriculum/lab4/upload-material")
    assert res.status_code == 422


def test_storage_key_follows_the_convention(client):
    upload(client, "week4.pdf", "pointer basics")
    stored = client.app.state.file_store.list_for_lab("lab4")[0]
    assert stored.storage_key == f"labs/csc580/lab4/{stored.file_id}/week4.pdf"
