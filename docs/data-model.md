# Task 2 — Lab materials & file uploads: data model

Scope: Instructor UI + Curriculum Designer. What the agent takes in, what it gives
back, and the MongoDB collections that replace the in-memory store.

Owner: Shreevatson Babu Sreegeetha

---

## 1. What goes in and what comes out

Every endpoint below is on the Curriculum Designer service (`routers/curriculum.py`,
prefix `/curriculum`). The canonical object is `LabMaterial` (`models/curriculum.py`).

| Input | Endpoint | Output |
|---|---|---|
| lab_id, course_id, title, learning_objectives[], difficulty, estimated_duration_min, instructor_id, material_weight | `POST /generate` | `LabMaterial` (version 1, `approval_status="pending"`) |
| same fields as form data + one or more PDFs (multipart) | `POST /generate-with-material` | `LabMaterial` |
| one or more PDFs (multipart) | `POST /{lab_id}/upload-material` | `{lab_id, field:"material_content", chars_stored, message}` |
| `{instructions}` | `POST /{lab_id}/upload-instructions` | `{lab_id, field:"agent_instructions", chars_stored, message}` |
| — | `GET /{lab_id}` | `LabMaterial` |
| `{approved_by, notes}` | `POST /{lab_id}/approve` | `LabMaterial`, `approval_status="approved"` |
| `{feedback, requested_by}` | `POST /{lab_id}/request-changes` | `LabMaterial`, `version+1`, quiz + rubric regenerated, spec preserved, `feedback_history` appended |
| — | `POST /{lab_id}/check-typos` | `{issues_found, issues[]}` — read-only, stores nothing |
| — | `GET /{lab_id}/export/{lab,quiz,rubric}.pdf` | PDF bytes, rendered on the fly, not stored |

### `LabMaterial` fields

| Field | Type | Notes |
|---|---|---|
| `lab_id` | str | Unique **within a course**, not globally — see §4 |
| `course_id` | str | Defaults to `"csc580"` |
| `title` | str | |
| `spec_markdown` | str | The lab handout |
| `quiz` | `QuizQuestion[]` | id, question, type, expected_answer, rubric_points, difficulty, learning_objective_ref |
| `rubric` | `Rubric` | code/report/manual weights + `criteria[]` of {name, weight, description} |
| `learning_objectives` | str[] | |
| `difficulty`, `estimated_duration_min`, `material_weight` | str/int/str | Generation parameters |
| `material_content` | str? | **Extracted text** of uploaded PDFs, merged into one string |
| `agent_instructions` | str? | Freeform instructor text |
| `approval_status` | `pending` \| `approved` \| `needs_changes` | |
| `approved_by`, `approval_notes` | str? | |
| `feedback_history` | `FeedbackEntry[]` | feedback, requested_by, requested_at, resulting_version |
| `version` | int | Bumped by `request-changes` |
| `generated_at`, `last_updated` | datetime | |

---

## 2. What is missing today

1. **Nothing persists.** `services/storage.py` defines a `CurriculumStore` interface
   (`get` / `put` / `list` / `delete`) with one implementation, `MemoryStore`, a dict
   behind a lock. Restart the service and every lab is gone. The `build_store()`
   factory already reserves a second backend, so a Mongo implementation drops in
   without touching the routers.
2. **Uploaded files are discarded.** `upload-material` parses each PDF to text, merges
   it into `material_content`, and throws the bytes away. Filename, size, uploader and
   upload time are never recorded, so the instructor UI cannot list "files attached to
   this lab", and a re-generate cannot cite its sources.
3. **The instructor UI is not wired to this service.** `src/api/agents.ts` only has
   dashboard, integrity and grading calls, all pointed at the Orchestrator. There are
   no curriculum calls at all, and `UploadMaterialModal` / `UploadAgentModal` close
   without uploading anything (`UploadMaterialModal.tsx:76`).

---

## 3. Proposed collections

Database `aied`. Two collections.

### `labs`

One document per lab, the `LabMaterial` shape unchanged so the Pydantic model
round-trips with no translation layer.

```jsonc
{
  "_id": ObjectId,
  "lab_id": "lab4",
  "course_id": "csc580",
  "title": "Linked Lists",
  "spec_markdown": "# Lab 4 ...",
  "quiz": [ { "id": "q1", "question": "...", "type": "short_answer",
              "expected_answer": "...", "rubric_points": 10,
              "difficulty": "basic", "learning_objective_ref": "..." } ],
  "rubric": { "code_weight": 0.6, "report_weight": 0.3, "manual_weight": 0.1,
              "criteria": [ { "name": "Correctness", "weight": 0.5,
                              "description": "..." } ] },
  "learning_objectives": ["..."],
  "difficulty": "intermediate",
  "estimated_duration_min": 90,
  "material_weight": "balanced",
  "material_content": "merged extracted text ...",
  "agent_instructions": "...",
  "approval_status": "pending",
  "approved_by": null,
  "approval_notes": null,
  "feedback_history": [ { "feedback": "...", "requested_by": "instructor_1",
                          "requested_at": ISODate, "resulting_version": 2 } ],
  "version": 1,
  "generated_at": ISODate,
  "last_updated": ISODate
}
```

`feedback_history` stays embedded: it is small, bounded by how many times an
instructor asks for changes, and it is always read together with the lab.

### `lab_files`

One document per uploaded file. New — nothing like it exists today.

```jsonc
{
  "_id": ObjectId,
  "file_id": "uuid4",
  "lab_id": "lab4",
  "course_id": "csc580",
  "filename": "week4-lecture.pdf",
  "content_type": "application/pdf",
  "size_bytes": 184320,
  "kind": "material",            // "material" | "instructions"
  "uploaded_by": "instructor_1",
  "uploaded_at": ISODate,
  "storage_key": "labs/csc580/lab4/<file_id>/week4-lecture.pdf",
  "extracted_text": "full text of this one file ...",
  "char_count": 18244
}
```

**Why a separate collection rather than an array on the lab:**

- MongoDB caps a document at 16 MB. `material_content` is the one field with no
  natural bound — several lecture PDFs merged. Per-file text in its own document
  keeps the lab document small and predictable.
- The instructor UI wants to *list* files (name, size, date, delete). That is a query,
  not a field.
- Re-generating can cite which file a passage came from, because text is no longer
  merged beyond recovery.

`material_content` stays on the lab document as the merged string the generation
graph already consumes. It becomes a derived value: concatenate the `extracted_text`
of this lab's `kind: "material"` files, in upload order. No prompt code changes.

### `storage_key` and the hand-off to task 3

The bytes are **not** stored in MongoDB. `storage_key` is a pointer into the shared
file storage Rocio is building in task 3. Proposed convention:

```
labs/{course_id}/{lab_id}/{file_id}/{filename}
```

Stable, collision-free (file_id is a UUID), and human-readable in a bucket listing.
If task 3 lands on a different scheme, only this one string changes — nothing else in
the schema depends on its shape.

### Indexes

| Collection | Index | Why |
|---|---|---|
| `labs` | `{course_id: 1, lab_id: 1}` **unique** | The real primary key — see §4 |
| `labs` | `{course_id: 1, approval_status: 1}` | "show me labs awaiting approval" |
| `lab_files` | `{course_id: 1, lab_id: 1, uploaded_at: -1}` | List files for a lab, newest first |
| `lab_files` | `{file_id: 1}` **unique** | Fetch/delete one file |

---

## 4. One bug worth fixing during the migration

The current code treats `lab_id` as globally unique: `store.get(lab_id)`,
`store.put(material)` keyed on `material.lab_id` alone. But `course_id` exists and
defaults to `"csc580"`. Two courses that each have a `lab4` would overwrite each
other.

The migration is the cheap moment to fix it: make `(course_id, lab_id)` the unique
key in Mongo, and have the store methods take both. Fixing it later means a data
migration instead of an index definition.

---

## 5. What this branch implements

| Change | Where |
|---|---|
| `MongoStore` — `labs` collection, indexes created on startup | `services/storage.py` |
| `MongoFileStore` — `lab_files` collection | `services/storage.py` |
| `LabFile` / `LabFileSummary` / `build_storage_key()` | `models/files.py` (new) |
| Uploads recorded as file documents instead of merged and discarded | `routers/curriculum.py` |
| `GET /curriculum/{lab_id}/files` — list attachments (metadata only) | `routers/curriculum.py` |
| `DELETE /curriculum/{lab_id}/files/{file_id}` — detach and rebuild `material_content` | `routers/curriculum.py` |
| `(course_id, lab_id)` is now the store key; `course_id` optional on reads | `services/storage.py` |
| 31 tests, no MongoDB server needed (mongomock + in-memory) | `tests/` |

`material_content` became a derived value: the `extracted_text` of this lab's
`material` files, joined in upload order, byte-identical to what the router used
to build inline. The generation prompts did not change.

### Turning it on

```bash
pip install -r requirements.txt
```

```ini
# .env
STORAGE_BACKEND=mongo
MONGODB_URI=mongodb+srv://<user>:<pass>@<cluster>/?retryWrites=true&w=majority
MONGODB_DB=aied
```

`STORAGE_BACKEND=memory` (the default) still works and needs no connection
string, so tests and local dev are unaffected. Run the tests with:

```bash
pytest tests/ -q
```

---

## 6. Open questions for the team

1. **Version rollback.** Right now a regeneration overwrites quiz and rubric in place
   and only the feedback text is kept in `feedback_history`. If instructors should be
   able to view or restore version 3 after version 4 exists, we need a `lab_versions`
   collection holding a snapshot per version. Not building it yet — flagging the cost
   of adding it later.
2. **`storage_key` format** — needs a yes/no from Rocio (task 3).
3. **Who owns `course_id`?** No courses collection exists anywhere. If one is coming
   out of task 1 or 3, `labs.course_id` should reference it.
4. **Non-PDF uploads.** The UI's dropzone accepts `.pdf,.doc,.docx,.txt`, but the
   service only parses PDFs and returns `PDF_PARSE_ERROR` otherwise. Either narrow
   the UI to PDF or add parsers.
5. **Upstash.** Dr. Kalathas set up an Upstash account alongside MongoDB. Nothing in
   task 2 needs a cache or queue yet; worth confirming what it is intended for.
