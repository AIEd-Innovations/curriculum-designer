"""
Storage abstraction.

Backends:
  memory  — dict behind a lock. Fine for tests and local dev; loses everything
            on restart.
  mongo   — MongoDB (`labs` and `lab_files` collections). See docs/data-model.md.

The interface stays synchronous. Queries here are single-document lookups by an
indexed key (single-digit milliseconds), while the endpoints that call them
spend seconds inside LLM calls, so the event loop cost is not worth the churn of
making every router `await` the store.

A lab is identified by (course_id, lab_id), not lab_id alone — two courses can
each have a "lab4". `course_id` is optional on reads so existing callers that
only know the lab_id keep working; pass it once the API carries it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from threading import Lock

from models.curriculum import LabMaterial
from models.files import LabFile
from services.schema import COLLECTIONS


# ── Lab materials ─────────────────────────────────────────────────────────────

class CurriculumStore(ABC):
    """Abstract base. All implementations must be safe for concurrent calls."""

    @abstractmethod
    def get(self, lab_id: str, course_id: str | None = None) -> LabMaterial | None: ...

    @abstractmethod
    def put(self, material: LabMaterial) -> None: ...

    @abstractmethod
    def list(self, course_id: str | None = None) -> list[LabMaterial]: ...

    @abstractmethod
    def delete(self, lab_id: str, course_id: str | None = None) -> bool: ...


class MemoryStore(CurriculumStore):
    """In-memory dict-backed store. Good enough for v0.1 + tests."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], LabMaterial] = {}
        self._lock = Lock()

    def _find_key(self, lab_id: str, course_id: str | None) -> tuple[str, str] | None:
        if course_id is not None:
            key = (course_id, lab_id)
            return key if key in self._data else None
        # No course given: fall back to the first lab with this id.
        return next((k for k in self._data if k[1] == lab_id), None)

    def get(self, lab_id: str, course_id: str | None = None) -> LabMaterial | None:
        with self._lock:
            key = self._find_key(lab_id, course_id)
            return self._data.get(key) if key else None

    def put(self, material: LabMaterial) -> None:
        with self._lock:
            self._data[(material.course_id, material.lab_id)] = material

    def list(self, course_id: str | None = None) -> list[LabMaterial]:
        with self._lock:
            return [
                m for (c, _), m in self._data.items()
                if course_id is None or c == course_id
            ]

    def delete(self, lab_id: str, course_id: str | None = None) -> bool:
        with self._lock:
            key = self._find_key(lab_id, course_id)
            return self._data.pop(key, None) is not None if key else False


class MongoStore(CurriculumStore):
    """MongoDB-backed store for the `labs` collection."""

    def __init__(self, uri: str, db_name: str, client=None) -> None:
        if client is None:
            from pymongo import MongoClient

            client = MongoClient(uri, appname="curriculum-designer")
        self._client = client
        self._labs = client[db_name]["labs"]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        # Defined once in services/schema.py, alongside the validators and the
        # tables in docs/schema.md.
        for keys, unique, _why in COLLECTIONS["labs"]["indexes"]:
            self._labs.create_index(keys, unique=unique)

    @staticmethod
    def _to_document(material: LabMaterial) -> dict:
        return material.model_dump(mode="python")

    @staticmethod
    def _from_document(doc: dict | None) -> LabMaterial | None:
        if doc is None:
            return None
        doc.pop("_id", None)
        return LabMaterial(**doc)

    def _filter(self, lab_id: str, course_id: str | None) -> dict:
        query: dict = {"lab_id": lab_id}
        if course_id is not None:
            query["course_id"] = course_id
        return query

    def get(self, lab_id: str, course_id: str | None = None) -> LabMaterial | None:
        return self._from_document(self._labs.find_one(self._filter(lab_id, course_id)))

    def put(self, material: LabMaterial) -> None:
        self._labs.replace_one(
            {"course_id": material.course_id, "lab_id": material.lab_id},
            self._to_document(material),
            upsert=True,
        )

    def list(self, course_id: str | None = None) -> list[LabMaterial]:
        query = {} if course_id is None else {"course_id": course_id}
        return [
            m for m in (self._from_document(doc) for doc in self._labs.find(query))
            if m is not None
        ]

    def delete(self, lab_id: str, course_id: str | None = None) -> bool:
        return self._labs.delete_one(self._filter(lab_id, course_id)).deleted_count > 0

    def close(self) -> None:
        self._client.close()


# ── Uploaded files ────────────────────────────────────────────────────────────

class LabFileStore(ABC):
    """Metadata for files attached to a lab. The bytes live in shared storage."""

    @abstractmethod
    def add(self, file: LabFile) -> None: ...

    @abstractmethod
    def get(self, file_id: str) -> LabFile | None: ...

    @abstractmethod
    def list_for_lab(
        self, lab_id: str, course_id: str | None = None, kind: str | None = None
    ) -> list[LabFile]: ...

    @abstractmethod
    def delete(self, file_id: str) -> bool: ...

    def merged_text(self, lab_id: str, course_id: str | None = None) -> str:
        """Rebuild `LabMaterial.material_content` from its source files.

        Same layout the router used when it merged PDFs inline, so prompts that
        already consume material_content see no change.
        """
        files = self.list_for_lab(lab_id, course_id, kind="material")
        if not files:
            return ""
        sections = [
            f"[Document {i}: {f.filename}]\n{f.extracted_text}"
            for i, f in enumerate(files, start=1)
        ]
        return "\n\n" + ("-" * 64 + "\n\n").join(sections)


class MemoryFileStore(LabFileStore):
    def __init__(self) -> None:
        self._data: dict[str, LabFile] = {}
        self._lock = Lock()

    def add(self, file: LabFile) -> None:
        with self._lock:
            self._data[file.file_id] = file

    def get(self, file_id: str) -> LabFile | None:
        with self._lock:
            return self._data.get(file_id)

    def list_for_lab(
        self, lab_id: str, course_id: str | None = None, kind: str | None = None
    ) -> list[LabFile]:
        with self._lock:
            files = [
                f for f in self._data.values()
                if f.lab_id == lab_id
                and (course_id is None or f.course_id == course_id)
                and (kind is None or f.kind == kind)
            ]
        return sorted(files, key=lambda f: f.uploaded_at)

    def delete(self, file_id: str) -> bool:
        with self._lock:
            return self._data.pop(file_id, None) is not None


class MongoFileStore(LabFileStore):
    """MongoDB-backed store for the `lab_files` collection."""

    def __init__(self, uri: str, db_name: str, client=None) -> None:
        if client is None:
            from pymongo import MongoClient

            client = MongoClient(uri, appname="curriculum-designer")
        self._client = client
        self._files = client[db_name]["lab_files"]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        for keys, unique, _why in COLLECTIONS["lab_files"]["indexes"]:
            self._files.create_index(keys, unique=unique)

    @staticmethod
    def _from_document(doc: dict | None) -> LabFile | None:
        if doc is None:
            return None
        doc.pop("_id", None)
        return LabFile(**doc)

    def add(self, file: LabFile) -> None:
        self._files.replace_one(
            {"file_id": file.file_id}, file.model_dump(mode="python"), upsert=True
        )

    def get(self, file_id: str) -> LabFile | None:
        return self._from_document(self._files.find_one({"file_id": file_id}))

    def list_for_lab(
        self, lab_id: str, course_id: str | None = None, kind: str | None = None
    ) -> list[LabFile]:
        query: dict = {"lab_id": lab_id}
        if course_id is not None:
            query["course_id"] = course_id
        if kind is not None:
            query["kind"] = kind
        cursor = self._files.find(query).sort("uploaded_at", 1)
        return [f for f in (self._from_document(doc) for doc in cursor) if f is not None]

    def delete(self, file_id: str) -> bool:
        return self._files.delete_one({"file_id": file_id}).deleted_count > 0


# ── Factories ─────────────────────────────────────────────────────────────────

def _require_uri(uri: str) -> str:
    if not uri:
        raise ValueError(
            "STORAGE_BACKEND=mongo requires MONGODB_URI. Set it in .env "
            "(Atlas: Connect → Drivers → Python)."
        )
    return uri


def build_store(backend: str, uri: str = "", db_name: str = "aied") -> CurriculumStore:
    """Factory called from main.py during lifespan."""
    backend = backend.lower()
    if backend == "memory":
        return MemoryStore()
    if backend == "mongo":
        return MongoStore(_require_uri(uri), db_name)
    if backend == "cosmos":
        raise NotImplementedError(
            "Azure Cosmos was the original plan; the project moved to MongoDB. "
            "Set STORAGE_BACKEND=mongo."
        )
    raise ValueError(f"Unknown storage backend: {backend!r}")


def build_file_store(backend: str, uri: str = "", db_name: str = "aied") -> LabFileStore:
    """File metadata store, paired with the lab store above."""
    backend = backend.lower()
    if backend == "memory":
        return MemoryFileStore()
    if backend == "mongo":
        return MongoFileStore(_require_uri(uri), db_name)
    raise ValueError(f"Unknown storage backend: {backend!r}")
