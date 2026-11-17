"""Users and courses — the tenancy tables shared with the rest of the framework.

With `STORAGE_BACKEND=mongo` this is `aied_db.stores.system.MongoSystemStore`
reading the `users` / `courses` / `enrollments` collections of the shared
`aied` database. The memory backend gets the small stand-in below, which
implements only the handful of methods this service calls, so local runs and
the tests need neither MongoDB nor the `aied-db` package.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class MemorySystemStore:
    """In-memory users and courses, matching `MongoSystemStore`'s row shapes."""

    def __init__(self) -> None:
        self._users: dict[str, dict] = {}
        self._courses: dict[str, dict] = {}
        self._lock = Lock()

    # ------------------------------------------------------------- users
    def insert_user(self, item: dict) -> None:
        with self._lock:
            self._users[item["id"]] = {
                "id": item["id"],
                "email": item["email"],
                "name": item.get("name", ""),
                "sso_subject": item.get("sso_subject"),
                "role": item["role"],
                "created_at": _iso(item.get("created_at")),
            }

    def get_user(self, user_id: str) -> Optional[dict]:
        return self._users.get(user_id)

    def get_user_by_email(self, email: str) -> Optional[dict]:
        wanted = (email or "").lower()
        return next(
            (u for u in self._users.values() if (u["email"] or "").lower() == wanted), None
        )

    def get_user_by_sso_subject(self, subject: str) -> Optional[dict]:
        return next(
            (u for u in self._users.values() if u.get("sso_subject") == subject), None
        )

    def list_users(self) -> list[dict]:
        return list(self._users.values())

    # ----------------------------------------------------------- courses
    def insert_course(self, item: dict) -> None:
        with self._lock:
            self._courses[item["id"]] = {
                "id": item["id"],
                "name": item["name"],
                "instructor_id": item["instructor_id"],
                "blob_container_name": item.get("blob_container_name"),
                "search_index_name": item.get("search_index_name"),
                "search_indexer_name": item.get("search_indexer_name"),
                "search_datasource_name": item.get("search_datasource_name"),
                "created_at": _iso(item.get("created_at")),
            }

    def get_course(self, course_id: str) -> Optional[dict]:
        return self._courses.get(course_id)

    def list_courses(self, *, instructor_id: Optional[str] = None) -> list[dict]:
        return [
            c for c in self._courses.values()
            if instructor_id is None or c["instructor_id"] == instructor_id
        ]

    def delete_course(self, course_id: str) -> None:
        with self._lock:
            self._courses.pop(course_id, None)

    def delete_user(self, user_id: str) -> None:
        with self._lock:
            self._users.pop(user_id, None)

    def close(self) -> None:
        return None


def new_course(name: str, instructor_id: str, course_id: str) -> dict:
    """A `courses` row for a course created from the dashboard.

    The four Azure-only fields are left empty: blob containers and search
    indexes belong to the ingestion pipeline, not to a course an instructor
    types into a form. They are optional in the shared schema (see the
    `databases` repo), and empty strings keep older servers happy.
    """
    return {
        "id": course_id,
        "name": name,
        "instructor_id": instructor_id,
        "blob_container_name": "",
        "search_index_name": "",
        "search_indexer_name": "",
        "search_datasource_name": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def build_system_store(backend: str, uri: str = "", db_name: str = "aied"):
    """Factory called from main.py during lifespan."""
    backend = (backend or "").lower()
    if backend == "memory":
        return MemorySystemStore()
    if backend == "mongo":
        if not uri:
            raise ValueError(
                "STORAGE_BACKEND=mongo requires MONGODB_URI. Set it in .env "
                "(Atlas: Connect → Drivers → Python)."
            )
        from pymongo import MongoClient  # imported late: only the mongo path needs it

        try:
            from aied_db.stores.system import MongoSystemStore
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise RuntimeError(
                "STORAGE_BACKEND=mongo needs the shared database package. "
                "pip install -r requirements.txt (aied-db)."
            ) from exc

        client = MongoClient(uri, appname="curriculum-designer")
        return MongoSystemStore(client[db_name])
    raise ValueError(f"Unknown storage backend: {backend!r}")
