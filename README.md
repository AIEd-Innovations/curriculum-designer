# Curriculum Designer Agent

Microservice in the AIEIC Lab Multi-Agent System. Generates lab materials (spec, quiz, rubric) from instructor-provided learning objectives and optional PDF uploads. Supports instructor approval and feedback-driven regeneration.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # LLM_BACKEND=mock by default; add Azure credentials to use a real model
# .env.example selects MongoDB (STORAGE_BACKEND=mongo). For a quick local try without a database, set STORAGE_BACKEND=memory in .env.
python -m uvicorn main:app --host 127.0.0.1 --port 8003 --reload
```

Set `LLM_BACKEND=mock` in `.env` to run without Azure credentials. In mock mode generation returns fixed stub content (a placeholder spec and the same three quiz questions) and ignores `request-changes` feedback, so the version bumps but the quiz does not change.

`requirements.txt` pins `aieic-shared` from a git URL. Nothing in this service imports it, and the service runs without it; if the install fails because that repository is private, remove that line locally.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET  | `/health` | Health check |
| POST | `/curriculum/generate` | Generate lab from objectives; preserves any uploaded RAG context |
| POST | `/curriculum/generate-with-material` | Same as generate, as multipart form with optional PDF `files` (no `material_weight`) |
| GET  | `/curriculum/{lab_id}` | Fetch current `LabMaterial` |
| POST | `/curriculum/{lab_id}/upload-material` | Upload one or more PDFs → extract text as generation context |
| POST | `/curriculum/{lab_id}/upload-instructions` | Store instructor instructions as generation context |
| POST | `/curriculum/{lab_id}/approve` | Mark lab approved |
| POST | `/curriculum/{lab_id}/request-changes` | Inject feedback → regenerate quiz + rubric |
| POST | `/curriculum/{lab_id}/check-typos` | LLM proofread across spec, quiz, rubric |
| GET  | `/curriculum/{lab_id}/export/lab.pdf` | Download lab spec and metadata as PDF |
| GET  | `/curriculum/{lab_id}/export/quiz.pdf` | Download quiz as PDF |
| GET  | `/curriculum/{lab_id}/export/rubric.pdf` | Download rubric as PDF |

## Architecture

```
main.py                  App entry point; lifespan inits store + llm_client + graph
config.py                pydantic-settings (reads from .env)
models/curriculum.py     LabMaterial, QuizQuestion, Rubric, upload/response models
routers/curriculum.py    All endpoints
services/
  storage.py             MemoryStore (default) or MongoStore (STORAGE_BACKEND=mongo); see docs/data-model.md
  llm.py                 AzureLLMClient / MockLLMClient; build_llm_client() factory
  prompts.py             Prompt builders for 5 LLM tasks (return system, user tuples)
graphs/generation.py     LangGraph pipeline: spec → quiz → rubric → self_review
tests/                   Stage D (not yet implemented)
doc/
  plan.md                Design decisions and stage breakdown
  handoff.md             Session handoff — current status and where to look for what
  errors-log.md          Known pitfalls and fixes
```

**Generation flow:** `spec_generator → quiz_generator → rubric_generator → self_review`. Self-review issues trigger one retry back to `quiz_generator`. On `request-changes`, spec is preserved and only quiz+rubric are regenerated.

**RAG:** PDF text is extracted with `pypdf` and stored as `material_content` on the `LabMaterial` record. On generation, both `material_content` and `agent_instructions` are injected verbatim into every LLM prompt. No vector store.

## Deployment

```bash
docker build -t curriculum-designer .
docker run --env-file .env -p 8003:8003 curriculum-designer
```

Target: Azure Container Apps.

Deployment notes:
- **Health check:** `GET /health`. The service listens on `0.0.0.0:8003`.
- **Environment:** `LLM_BACKEND`, `AZURE_OPENAI_*`, `STORAGE_BACKEND` and, for MongoDB, `MONGODB_URI` / `MONGODB_DB` (see `.env.example`). Pass them with `--env` or `--env-file`. `docker run --env-file .env` fails if that file does not exist, so use `--env LLM_BACKEND=mock` for a quick try. Keep real credentials out of the image: add `.env` to a `.dockerignore` before building, since `COPY . .` would otherwise bake a local `.env` into it.
- **Storage:** with `STORAGE_BACKEND=memory` (the code default) a restart loses all labs and more than one replica would each hold different labs, so run a single replica. With `STORAGE_BACKEND=mongo` labs and uploaded files persist in MongoDB (set `MONGODB_URI`, keep the connection string out of the image and out of git).
- **Generation is synchronous** and can take 30-60 seconds on a real model, so raise any proxy or ingress timeout above that.
- **CORS** currently allows every origin (`allow_origins=["*"]` in `main.py`, marked TODO). Restrict it before exposing the service publicly.
- There is no authentication; `instructor_id`, `approved_by` and `requested_by` are client-supplied strings.
