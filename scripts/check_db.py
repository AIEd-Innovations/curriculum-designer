"""
Connection check for the MongoDB backend.

    python scripts/check_db.py

Reads .env, connects, creates the indexes, writes a throwaway lab and file,
reads them back, and deletes them. Prints what it found. Exits non-zero if
anything fails, so it can gate a deploy later.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from models.curriculum import LabMaterial, QuizQuestion, Rubric, RubricCriterion
from models.files import LabFile, build_storage_key
from services.storage import build_file_store, build_store

PROBE_COURSE = "__check_db__"
PROBE_LAB = "probe"


def fail(message: str) -> None:
    print(f"\n  FAILED: {message}\n")
    sys.exit(1)


def main() -> None:
    print(f"backend : {settings.storage_backend}")
    print(f"database: {settings.mongodb_db}")

    if settings.storage_backend != "mongo":
        fail("STORAGE_BACKEND is not 'mongo' — nothing to check. Set it in .env")
    if "PASTE_PASSWORD_HERE" in settings.mongodb_uri:
        fail("MONGODB_URI still has the placeholder password. Edit .env")
    if "<" in settings.mongodb_uri or ">" in settings.mongodb_uri:
        fail(
            "MONGODB_URI still has < > around the password. Atlas gives you a "
            "template — remove the angle brackets."
        )

    # Hide the password when echoing the host back
    host = settings.mongodb_uri.split("@")[-1].split("/")[0]
    print(f"cluster : {host}\n")

    try:
        store = build_store(
            settings.storage_backend, settings.mongodb_uri, settings.mongodb_db
        )
        files = build_file_store(
            settings.storage_backend, settings.mongodb_uri, settings.mongodb_db
        )
    except Exception as exc:
        fail(f"could not connect — {type(exc).__name__}: {exc}")

    print("  connected, indexes created")

    now = datetime.now(timezone.utc)
    lab = LabMaterial(
        lab_id=PROBE_LAB,
        course_id=PROBE_COURSE,
        title="Connection probe",
        spec_markdown="# probe",
        quiz=[
            QuizQuestion(
                id="q1",
                question="probe?",
                type="short_answer",
                expected_answer="yes",
                rubric_points=1,
                difficulty="basic",
            )
        ],
        rubric=Rubric(criteria=[RubricCriterion(name="n", weight=1.0, description="d")]),
        learning_objectives=["probe"],
        generated_at=now,
        last_updated=now,
    )

    try:
        store.put(lab)
        read_back = store.get(PROBE_LAB, PROBE_COURSE)
        if read_back is None or read_back.quiz[0].expected_answer != "yes":
            fail("wrote a lab but could not read it back intact")
        print("  labs: write + read + nested models OK")

        probe_file = LabFile(
            file_id="probe-file",
            lab_id=PROBE_LAB,
            course_id=PROBE_COURSE,
            filename="probe.pdf",
            size_bytes=5,
            uploaded_at=now,
            storage_key=build_storage_key(PROBE_COURSE, PROBE_LAB, "probe-file", "probe.pdf"),
            extracted_text="probe",
            char_count=5,
        )
        files.add(probe_file)
        if not files.list_for_lab(PROBE_LAB, PROBE_COURSE):
            fail("wrote a file record but could not list it")
        print("  lab_files: write + list OK")

        existing = [
            m.lab_id for m in store.list() if m.course_id != PROBE_COURSE
        ]
        print(f"\n  real labs already stored: {len(existing)}"
              + (f" → {existing}" if existing else ""))
    finally:
        files.delete("probe-file")
        store.delete(PROBE_LAB, PROBE_COURSE)

    print("\n  probe rows cleaned up. Database is ready.\n")


if __name__ == "__main__":
    main()
