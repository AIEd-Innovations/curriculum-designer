"""scripts/init_db.py must not be run against the shared database by accident.

databases/scripts/init_db.py owns the schemas on `aied`; this script only knows this agent's two
collections, so running it there can overwrite validators the shared script set.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "init_db.py"


@pytest.fixture
def init_db():
    spec = importlib.util.spec_from_file_location("init_db_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_shared_database_is_refused_without_the_flag(init_db):
    reason = init_db.shared_database_refusal("aied", ["init_db.py"])
    assert reason and "shared database" in reason and "--shared" in reason


def test_the_flag_allows_it(init_db):
    assert init_db.shared_database_refusal("aied", ["init_db.py", "--shared"]) is None


def test_any_other_database_is_fine(init_db):
    assert init_db.shared_database_refusal("aied_jeron", ["init_db.py"]) is None


def test_apply_exits_before_touching_anything(init_db, monkeypatch):
    monkeypatch.setattr(init_db.settings, "mongodb_db", "aied")
    monkeypatch.setattr(init_db.sys, "argv", ["init_db.py"])
    with pytest.raises(SystemExit) as stopped:
        init_db.apply()
    assert "shared database" in str(stopped.value)
