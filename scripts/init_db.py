"""
Apply the collection schemas and indexes to MongoDB.

    python scripts/init_db.py          # apply
    python scripts/init_db.py --show   # print them, touch nothing

Idempotent: creates each collection with its validator the first time, and
updates the validator with collMod afterwards. Safe to run on every deploy.

Validation level is "moderate": new documents and updates to valid documents
must match the schema, but an existing document that predates it is not
rejected on read. Switch to "strict" in services/schema.py once the data is
known to be clean.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from services.schema import COLLECTIONS, ensure_all, validator_for


def show() -> None:
    for name, spec in COLLECTIONS.items():
        print(f"\n=== {name} ===")
        print(json.dumps(validator_for(name), indent=2, default=str))
        print("indexes:")
        for keys, unique, why in spec["indexes"]:
            flag = " (unique)" if unique else ""
            print(f"  {keys}{flag} — {why}")


def apply() -> None:
    if settings.storage_backend != "mongo":
        sys.exit("STORAGE_BACKEND is not 'mongo'. Set it in .env first.")
    if not settings.mongodb_uri or "PASTE_PASSWORD_HERE" in settings.mongodb_uri:
        sys.exit("MONGODB_URI is unset or still has the placeholder password. Edit .env.")
    if "<" in settings.mongodb_uri or ">" in settings.mongodb_uri:
        sys.exit("MONGODB_URI still has < > around the password — remove the angle brackets.")

    from pymongo import MongoClient

    host = settings.mongodb_uri.split("@")[-1].split("/")[0]
    print(f"cluster : {host}")
    print(f"database: {settings.mongodb_db}\n")

    client = MongoClient(settings.mongodb_uri, appname="curriculum-designer-init")
    try:
        results = ensure_all(client[settings.mongodb_db])
    except Exception as exc:
        sys.exit(f"\n  FAILED: {type(exc).__name__}: {exc}\n")

    for name, action in results.items():
        index_count = len(COLLECTIONS[name]["indexes"])
        print(f"  {name:<12} {action:<8} {index_count} index(es)")

    print("\n  Schema applied.\n")
    client.close()


if __name__ == "__main__":
    if "--show" in sys.argv:
        show()
    else:
        apply()
