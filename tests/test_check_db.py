"""Tests for scripts/check_db.py — the script a teammate runs to confirm their
connection string works. Exercised against mongomock, so the happy path is
verified without a cluster."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

mongomock = pytest.importorskip("mongomock")

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_db.py"


@pytest.fixture
def check_db(monkeypatch):
    import pymongo

    monkeypatch.setattr(pymongo, "MongoClient", mongomock.MongoClient)

    spec = importlib.util.spec_from_file_location("check_db", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configure(module, monkeypatch, **overrides):
    defaults = {
        "storage_backend": "mongo",
        "mongodb_uri": "mongodb://user:pw@fake-cluster.mongodb.net/",
        "mongodb_db": "aied_test",
    }
    for key, value in {**defaults, **overrides}.items():
        monkeypatch.setattr(module.settings, key, value, raising=False)


def test_happy_path_reports_ready(check_db, monkeypatch, capsys):
    configure(check_db, monkeypatch)
    check_db.main()

    out = capsys.readouterr().out
    assert "connected, indexes created" in out
    assert "labs: write + read + nested models OK" in out
    assert "lab_files: write + list OK" in out
    assert "Database is ready" in out


def test_password_is_never_printed(check_db, monkeypatch, capsys):
    configure(check_db, monkeypatch)
    check_db.main()
    assert "pw" not in capsys.readouterr().out.replace("password", "")


def test_probe_rows_are_cleaned_up(check_db, monkeypatch, capsys):
    configure(check_db, monkeypatch)
    check_db.main()

    store = check_db.build_store("mongo", "mongodb://fake/", "aied_test")
    assert store.get(check_db.PROBE_LAB, check_db.PROBE_COURSE) is None


def test_placeholder_password_is_caught(check_db, monkeypatch, capsys):
    configure(
        check_db,
        monkeypatch,
        mongodb_uri="mongodb+srv://user:PASTE_PASSWORD_HERE@c.mongodb.net/",
    )
    with pytest.raises(SystemExit) as exc:
        check_db.main()

    assert exc.value.code == 1
    assert "placeholder password" in capsys.readouterr().out


def test_angle_brackets_are_caught(check_db, monkeypatch, capsys):
    """Atlas hands out a template with <db_password> — pasting it unedited is
    the most likely mistake a teammate will make."""
    configure(
        check_db,
        monkeypatch,
        mongodb_uri="mongodb+srv://user:<db_password>@c.mongodb.net/",
    )
    with pytest.raises(SystemExit) as exc:
        check_db.main()

    assert exc.value.code == 1
    assert "angle brackets" in capsys.readouterr().out


def test_memory_backend_is_rejected(check_db, monkeypatch, capsys):
    configure(check_db, monkeypatch, storage_backend="memory")
    with pytest.raises(SystemExit):
        check_db.main()

    assert "not 'mongo'" in capsys.readouterr().out
