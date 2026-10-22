# Curriculum Designer Agent

Microservice in the AIEIC Lab Multi-Agent System. Generates lab materials (spec, quiz, rubric) from instructor-provided learning objectives and optional PDF uploads. Supports instructor approval and feedback-driven regeneration.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in Azure credentials
python -m uvicorn main:app --host 127.0.0.1 --port 8003 --reload
```

Set `LLM_BACKEND=mock` in `.env` to run without Azure credentials.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET  | `/health` | Health check |
| GET  | `/curriculum/models` | Models a teacher can pick when using their own API key |
| POST | `/curriculum/generate` | Generate lab from objectives; preserves any uploaded RAG context |
| GET  | `/curriculum/{lab_id}` | Fetch current `LabMaterial` |
| POST | `/curriculum/{lab_id}/upload-material` | Upload one or more PDFs → extract text as generation context |
| POST | `/curriculum/{lab_id}/upload-instructions` | Store instructor instructions as generation context |
| POST | `/curriculum/{lab_id}/approve` | Mark lab approved |
| POST | `/curriculum/{lab_id}/request-changes` | Inject feedback → regenerate quiz + rubric |
| POST | `/curriculum/{lab_id}/check-typos` | LLM proofread across spec, quiz, rubric |
| GET  | `/curriculum/{lab_id}/export/lab.pdf` | Download lab spec and metadata as PDF |
| GET  | `/curriculum/{lab_id}/export/quiz.pdf` | Download quiz as PDF |
| GET  | `/curriculum/{lab_id}/export/rubric.pdf` | Download rubric as PDF |

## Teacher API keys

Any endpoint that calls the model (`generate`, `generate-with-material`, `request-changes`, `check-typos`) accepts the teacher's own key:

| Header | Meaning |
|---|---|
| `X-LLM-API-Key` | The teacher's key for the vendor of the chosen model |
| `X-LLM-Model` | A model id from `GET /curriculum/models` (required whenever a key is sent) |

The key is used for that request only. It is never stored or logged, and it is scrubbed from error messages. Without the headers the server's own backend (`LLM_BACKEND`, Azure by default) is used. A key the vendor rejects returns `401` (`LLM_KEY_REJECTED`); any other model failure returns `502` (`LLM_ERROR`). The model list matches the assessment agent's.

Keys travel in headers, so serve this over HTTPS and restrict the CORS wildcard (`allow_origins=["*"]` in `main.py`) to the UI's origin before exposing it.

## Architecture

```
main.py                  App entry point; lifespan inits store + llm_client + graph
config.py                pydantic-settings (reads from .env)
models/curriculum.py     LabMaterial, QuizQuestion, Rubric, upload/response models
routers/curriculum.py    All endpoints
services/
  storage.py             MemoryStore (default) — swap to CosmosStore in v0.2
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
