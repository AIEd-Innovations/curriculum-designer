"""Startup smoke tests.

The real app object, real lifespan: stores built, LLM client built, generation
graph compiled, routes registered. Catches wiring mistakes that unit tests on
the storage layer cannot see.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(monkeypatch):
    # Default backend, so no MongoDB and no Azure credentials are needed.
    monkeypatch.setenv("STORAGE_BACKEND", "memory")
    monkeypatch.setenv("LLM_BACKEND", "mock")

    import config
    import importlib

    importlib.reload(config)

    import main

    importlib.reload(main)
    return main.app


def test_app_starts_and_is_healthy(app):
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "healthy"


def test_lifespan_builds_both_stores(app):
    from services.storage import CurriculumStore, LabFileStore

    with TestClient(app):
        assert isinstance(app.state.store, CurriculumStore)
        assert isinstance(app.state.file_store, LabFileStore)


def test_generation_graph_compiled(app):
    with TestClient(app):
        assert app.state.generation_graph is not None


def registered_paths(app) -> set[str]:
    """Read paths off the OpenAPI schema — app.routes nests included routers
    differently across FastAPI versions."""
    return set(app.openapi()["paths"])


def test_file_routes_are_registered(app):
    paths = registered_paths(app)
    assert "/curriculum/{lab_id}/files" in paths
    assert "/curriculum/{lab_id}/files/{file_id}" in paths


def test_existing_routes_still_registered(app):
    """The migration must not have dropped anything."""
    paths = registered_paths(app)
    for path in (
        "/curriculum/generate",
        "/curriculum/generate-with-material",
        "/curriculum/{lab_id}",
        "/curriculum/{lab_id}/upload-material",
        "/curriculum/{lab_id}/upload-instructions",
        "/curriculum/{lab_id}/approve",
        "/curriculum/{lab_id}/request-changes",
        "/curriculum/{lab_id}/check-typos",
        "/curriculum/{lab_id}/export/lab.pdf",
        "/curriculum/{lab_id}/export/quiz.pdf",
        "/curriculum/{lab_id}/export/rubric.pdf",
    ):
        assert path in paths, f"route disappeared: {path}"
