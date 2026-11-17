"""
Connection check for the MongoDB backend.

    python scripts/check_db.py

Reads .env, connects, creates the indexes, writes a throwaway lab, file, user
and course, reads them back through a second connection (so what survives is
the database, not this process), and deletes them. Prints what it found.
Exits non-zero if anything fails, so it can gate a deploy.

This is the check behind "deploy with STORAGE_BACKEND=mongo and make sure the
labs are still there after a restart" (issue #16, §4).
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
from services.system import build_system_store, new_course

PROBE_COURSE = "__check_db__"
PROBE_LAB = "probe"
PROBE_USER = "__check_db__user"
PROBE_SSO = "__check_db__oid"


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
        system = build_system_store(
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

        # Who owns what: the tables /me and the ownership check read.
        system.insert_user(
            {
                "id": PROBE_USER,
                "email": "check-db@calpoly.edu",
                "name": "Connection probe",
                "sso_subject": PROBE_SSO,
                "role": "instructor",
                "created_at": now.isoformat(),
            }
        )
        found = system.get_user_by_sso_subject(PROBE_SSO)
        if found is None or found["id"] != PROBE_USER:
            fail("wrote a user but could not find them by their Microsoft oid")

        system.insert_course(
            new_course("Connection probe", instructor_id=PROBE_USER, course_id=PROBE_COURSE)
        )
        owned = [c["id"] for c in system.list_courses(instructor_id=PROBE_USER)]
        if owned != [PROBE_COURSE]:
            fail(f"a course was written but the owner's course list reads {owned}")
        print("  users + courses: write + read by oid + list by owner OK")

        # A restart, as far as the database is concerned: new clients, new
        # connections, nothing kept in this process.
        reopened = build_store(
            settings.storage_backend, settings.mongodb_uri, settings.mongodb_db
        )
        reopened_system = build_system_store(
            settings.storage_backend, settings.mongodb_uri, settings.mongodb_db
        )
        if reopened.get(PROBE_LAB, PROBE_COURSE) is None:
            fail("the lab did not survive a fresh connection")
        if reopened_system.get_course(PROBE_COURSE) is None:
            fail("the course did not survive a fresh connection")
        print("  survives a restart: both read back on a second connection OK")

        existing = [
            m.lab_id for m in store.list() if m.course_id != PROBE_COURSE
        ]
        print(f"\n  real labs already stored: {len(existing)}"
              + (f" → {existing}" if existing else ""))
    finally:
        # Each on its own line: one failure must not leave the rest behind.
        for undo in (
            lambda: files.delete("probe-file"),
            lambda: store.delete(PROBE_LAB, PROBE_COURSE),
            lambda: system.delete_course(PROBE_COURSE),
            lambda: system.delete_user(PROBE_USER),
        ):
            try:
                undo()
            except Exception as exc:  # noqa: BLE001 - cleanup is best effort
                print(f"  (could not clean up a probe row: {exc})")

    print("\n  probe rows cleaned up. Database is ready.\n")


if __name__ == "__main__":
    main()
