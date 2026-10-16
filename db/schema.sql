-- Curriculum Designer — relational schema (PostgreSQL / Supabase)
--
-- The tabular form of models/curriculum.py and models/files.py.
-- Diagram: docs/erd.png   Tables and notes: docs/schema-relational.md
--
-- Run in the Supabase SQL editor, or:
--   psql "$DATABASE_URL" -f db/schema.sql
--
-- Idempotent: safe to run more than once.

begin;

-- ── Enumerations ─────────────────────────────────────────────────────────────
-- Postgres has no "create type if not exists", hence the guards.

do $$ begin
  create type approval_status as enum ('pending', 'approved', 'needs_changes');
exception when duplicate_object then null; end $$;

do $$ begin
  create type question_type as enum ('short_answer', 'multiple_choice', 'code', 'essay');
exception when duplicate_object then null; end $$;

do $$ begin
  create type difficulty_tier as enum ('basic', 'intermediate', 'challenge');
exception when duplicate_object then null; end $$;

do $$ begin
  create type material_weight as enum ('strict', 'balanced', 'expansive');
exception when duplicate_object then null; end $$;

do $$ begin
  create type file_kind as enum ('material', 'instructions');
exception when duplicate_object then null; end $$;

-- ── courses ──────────────────────────────────────────────────────────────────
-- New. Nothing in the project defined a course; course_id was a bare string
-- defaulting to 'csc580'. A lab must belong to one.

create table if not exists courses (
  course_id   text primary key,
  title       text not null,
  term        text
);

-- ── labs ─────────────────────────────────────────────────────────────────────
-- One row per lab. lab_key is the human id ("lab4"); it is unique per course,
-- not globally — two courses may each have a lab4.

create table if not exists labs (
  lab_id                 uuid primary key default gen_random_uuid(),
  course_id              text not null references courses(course_id) on delete restrict,
  lab_key                text not null,
  title                  text not null,
  spec_markdown          text not null default '',
  difficulty             difficulty_tier not null default 'intermediate',
  estimated_duration_min integer not null default 90 check (estimated_duration_min > 0),
  material_weight        material_weight not null default 'balanced',

  agent_instructions     text,

  approval_status        approval_status not null default 'pending',
  approved_by            text,
  approval_notes         text,

  -- Rubric weights are one-to-one with a lab, so they stay as columns rather
  -- than becoming a one-row table.
  code_weight            numeric(4,3) not null default 0.6 check (code_weight between 0 and 1),
  report_weight          numeric(4,3) not null default 0.3 check (report_weight between 0 and 1),
  manual_weight          numeric(4,3) not null default 0.1 check (manual_weight between 0 and 1),

  version                integer not null default 1 check (version >= 1),
  generated_at           timestamptz not null default now(),
  last_updated           timestamptz not null default now(),

  constraint labs_course_lab_key_unique unique (course_id, lab_key)
);

create index if not exists labs_course_approval_idx
  on labs (course_id, approval_status);

-- ── lab_learning_objectives ──────────────────────────────────────────────────
-- Was an array of strings on the lab. `position` preserves the order the
-- instructor entered them, which an array gave us for free.

create table if not exists lab_learning_objectives (
  objective_id uuid primary key default gen_random_uuid(),
  lab_id       uuid not null references labs(lab_id) on delete cascade,
  position     integer not null check (position >= 0),
  objective    text not null,

  constraint lab_objectives_position_unique unique (lab_id, position)
);

create index if not exists lab_objectives_lab_idx on lab_learning_objectives (lab_id);

-- ── lab_quiz_questions ───────────────────────────────────────────────────────
-- Was an embedded array. objective_id replaces learning_objective_ref, which
-- was a free string that had to match an objective exactly.

create table if not exists lab_quiz_questions (
  question_id     uuid primary key default gen_random_uuid(),
  lab_id          uuid not null references labs(lab_id) on delete cascade,
  label           text not null,                 -- "q1", "q2", …
  question        text not null,
  type            question_type not null,
  expected_answer text not null default '',
  rubric_points   integer not null default 0 check (rubric_points >= 0),
  difficulty      difficulty_tier not null,
  objective_id    uuid references lab_learning_objectives(objective_id) on delete set null,

  constraint lab_quiz_label_unique unique (lab_id, label)
);

create index if not exists lab_quiz_lab_idx on lab_quiz_questions (lab_id);

-- ── lab_rubric_criteria ──────────────────────────────────────────────────────

create table if not exists lab_rubric_criteria (
  criterion_id uuid primary key default gen_random_uuid(),
  lab_id       uuid not null references labs(lab_id) on delete cascade,
  name         text not null,
  weight       numeric(4,3) not null check (weight between 0 and 1),
  description  text not null default '',

  constraint lab_rubric_name_unique unique (lab_id, name)
);

create index if not exists lab_rubric_lab_idx on lab_rubric_criteria (lab_id);

-- ── lab_feedback_history ─────────────────────────────────────────────────────
-- Append-only. One row per instructor "request changes".

create table if not exists lab_feedback_history (
  feedback_id       uuid primary key default gen_random_uuid(),
  lab_id            uuid not null references labs(lab_id) on delete cascade,
  feedback          text not null,
  requested_by      text not null,
  requested_at      timestamptz not null default now(),
  resulting_version integer not null check (resulting_version >= 1)
);

create index if not exists lab_feedback_lab_idx
  on lab_feedback_history (lab_id, requested_at desc);

-- ── lab_files ────────────────────────────────────────────────────────────────
-- Metadata plus extracted text. The bytes live in shared file storage; the
-- pointer is storage_key.

create table if not exists lab_files (
  file_id        uuid primary key default gen_random_uuid(),
  lab_id         uuid not null references labs(lab_id) on delete cascade,
  filename       text not null,
  content_type   text not null default 'application/pdf',
  size_bytes     integer not null check (size_bytes >= 0),
  kind           file_kind not null default 'material',
  uploaded_by    text,
  uploaded_at    timestamptz not null default now(),
  storage_key    text not null unique,
  extracted_text text not null default '',
  char_count     integer not null default 0 check (char_count >= 0)
);

create index if not exists lab_files_lab_idx on lab_files (lab_id, uploaded_at);

-- ── material_content ─────────────────────────────────────────────────────────
-- In MongoDB this was a denormalised string on the lab, rebuilt by hand on
-- every upload and delete. Here it is a view, so it cannot go stale. The
-- format matches what the generation prompts already consume.

create or replace view lab_material_content as
select
  f.lab_id,
  E'\n\n' || string_agg(
    format('[Document %s: %s]' || E'\n' || '%s', f.ordinal, f.filename, f.extracted_text),
    repeat('-', 64) || E'\n\n' order by f.ordinal
  ) as material_content,
  sum(f.char_count)::integer as total_chars
from (
  select
    lab_id, filename, extracted_text, char_count,
    row_number() over (partition by lab_id order by uploaded_at, file_id) as ordinal
  from lab_files
  where kind = 'material'
) f
group by f.lab_id;

commit;

-- ── Row Level Security ───────────────────────────────────────────────────────
-- Supabase exposes every table over PostgREST, so RLS is not optional. These
-- tables hold instructor material and must not be world-readable. Enabled with
-- no policies = deny all, which is the safe default until auth exists; the
-- service role key used by the agent bypasses RLS.
--
-- Revisit when instructor accounts are real (Microsoft auth is post-integration
-- per ui-instructor#8) and add per-course policies then.

alter table courses                enable row level security;
alter table labs                   enable row level security;
alter table lab_learning_objectives enable row level security;
alter table lab_quiz_questions     enable row level security;
alter table lab_rubric_criteria    enable row level security;
alter table lab_feedback_history   enable row level security;
alter table lab_files              enable row level security;
