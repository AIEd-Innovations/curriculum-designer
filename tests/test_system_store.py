"""Users and courses on both backends, and labs that survive a restart.

`MemorySystemStore` has to behave like `aied_db`'s `MongoSystemStore`, because
`/me` and the ownership check are written against one shape. The Mongo half
runs on mongomock when `aied-db` is installed, and skips when it is not.
"""

from __future__ import annotations

from datetime import datetime, timezone

import mongomock
import pytest

from services.storage import MongoStore
from services.system import MemorySystemStore, build_system_store, new_course
from tests.test_mongo_store import make_lab  # a LabMaterial factory


def _mongo_system_store():
    MongoSystemStore = pytest.importorskip(
        "aied_db.stores.system", reason="aied-db is not installed"
    ).MongoSystemStore
    return MongoSystemStore(mongomock.MongoClient()["aied"])


def _user(uid: str, email: str, oid: str) -> dict:
    return {
        "id": uid,
        "email": email,
        "name": email.split("@")[0],
        "sso_subject": oid,
        "role": "instructor",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture(params=["memory", "mongo"])
def store(request):
    return MemorySystemStore() if request.param == "memory" else _mongo_system_store()


def test_a_user_is_found_by_their_microsoft_oid(store):
    store.insert_user(_user("u1", "alice@calpoly.edu", "oid-alice"))

    found = store.get_user_by_sso_subject("oid-alice")
    assert found["id"] == "u1"
    assert found["email"] == "alice@calpoly.edu"
    assert found["role"] == "instructor"
    assert store.get_user_by_sso_subject("oid-nobody") is None


def test_courses_are_listed_per_instructor(store):
    store.insert_user(_user("u1", "alice@calpoly.edu", "oid-alice"))
    store.insert_user(_user("u2", "bob@calpoly.edu", "oid-bob"))
    store.insert_course(new_course("CSC 580", instructor_id="u1", course_id="c1"))
    store.insert_course(new_course("CSC 581", instructor_id="u2", course_id="c2"))

    assert [c["name"] for c in store.list_courses(instructor_id="u1")] == ["CSC 580"]
    assert [c["name"] for c in store.list_courses(instructor_id="u2")] == ["CSC 581"]
    assert store.get_course("c1")["instructor_id"] == "u1"
    assert store.get_course("c3") is None


def test_a_dashboard_course_needs_no_azure_fields(store):
    """`insert_course` must not demand a blob container or a search index."""
    store.insert_course(new_course("CSC 580", instructor_id="u1", course_id="c1"))
    assert store.get_course("c1")["name"] == "CSC 580"


def test_build_system_store_picks_the_backend():
    assert isinstance(build_system_store("memory"), MemorySystemStore)
    with pytest.raises(ValueError, match="MONGODB_URI"):
        build_system_store("mongo")
    with pytest.raises(ValueError, match="Unknown storage backend"):
        build_system_store("sqlite")


def test_labs_are_still_there_after_a_restart():
    """`STORAGE_BACKEND=mongo` keeps labs in MongoDB, not in the process."""
    client = mongomock.MongoClient()
    MongoStore("", "aied", client=client).put(make_lab("lab1", course_id="c1"))

    # A new process, the same database.
    reopened = MongoStore("", "aied", client=client)
    assert reopened.get("lab1", "c1") is not None
    assert [m.lab_id for m in reopened.list("c1")] == ["lab1"]
    assert reopened.get("lab1", "another-course") is None
