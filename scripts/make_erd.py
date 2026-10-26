"""
Generate docs/erd.svg — the entity-relationship diagram for the relational
schema in docs/schema-relational.md.

    python scripts/make_erd.py

Laying it out in code keeps the diagram in step with the schema: add a column
here and regenerate, rather than nudging shapes in a drawing tool.
"""

from __future__ import annotations

from pathlib import Path

ROW_H = 26
HEADER_H = 46
PAD_BOTTOM = 14
FONT = "DejaVu Sans Mono, Menlo, Consolas, monospace"

# name -> (x, y, width, [(column, type, is_pk)])
TABLES: dict[str, tuple[int, int, int, list[tuple[str, str, bool]]]] = {
    "courses": (40, 40, 290, [
        ("course_id", "TEXT", True),
        ("title", "TEXT", False),
        ("term", "TEXT", False),
    ]),
    "lab_learning_objectives": (40, 250, 330, [
        ("objective_id", "UUID", True),
        ("lab_id", "UUID", False),
        ("position", "INT", False),
        ("objective", "TEXT", False),
    ]),
    "lab_files": (40, 470, 340, [
        ("file_id", "UUID", True),
        ("lab_id", "UUID", False),
        ("filename", "TEXT", False),
        ("content_type", "TEXT", False),
        ("size_bytes", "INT", False),
        ("kind", "ENUM", False),
        ("uploaded_by", "TEXT", False),
        ("uploaded_at", "TIMESTAMPTZ", False),
        ("storage_key", "TEXT", False),
        ("extracted_text", "TEXT", False),
        ("char_count", "INT", False),
    ]),
    "labs": (470, 40, 360, [
        ("lab_id", "UUID", True),
        ("course_id", "TEXT", False),
        ("lab_key", "TEXT", False),
        ("title", "TEXT", False),
        ("spec_markdown", "TEXT", False),
        ("difficulty", "ENUM", False),
        ("estimated_duration_min", "INT", False),
        ("material_weight", "ENUM", False),
        ("agent_instructions", "TEXT", False),
        ("approval_status", "ENUM", False),
        ("approved_by", "TEXT", False),
        ("approval_notes", "TEXT", False),
        ("code_weight", "NUMERIC", False),
        ("report_weight", "NUMERIC", False),
        ("manual_weight", "NUMERIC", False),
        ("version", "INT", False),
        ("generated_at", "TIMESTAMPTZ", False),
        ("last_updated", "TIMESTAMPTZ", False),
    ]),
    "lab_quiz_questions": (930, 40, 350, [
        ("question_id", "UUID", True),
        ("lab_id", "UUID", False),
        ("label", "TEXT", False),
        ("question", "TEXT", False),
        ("type", "ENUM", False),
        ("expected_answer", "TEXT", False),
        ("rubric_points", "INT", False),
        ("difficulty", "ENUM", False),
        ("objective_id", "UUID", False),
    ]),
    "lab_rubric_criteria": (930, 360, 350, [
        ("criterion_id", "UUID", True),
        ("lab_id", "UUID", False),
        ("name", "TEXT", False),
        ("weight", "NUMERIC", False),
        ("description", "TEXT", False),
    ]),
    "lab_feedback_history": (930, 590, 350, [
        ("feedback_id", "UUID", True),
        ("lab_id", "UUID", False),
        ("feedback", "TEXT", False),
        ("requested_by", "TEXT", False),
        ("requested_at", "TIMESTAMPTZ", False),
        ("resulting_version", "INT", False),
    ]),
}

# (from_table, from_column, from_side, to_table, to_column, to_side, elbow_x)
RELATIONS = [
    ("courses", "course_id", "right", "labs", "course_id", "left", 410),
    ("labs", "lab_id", "left", "lab_learning_objectives", "lab_id", "right", 420),
    ("labs", "lab_id", "left", "lab_files", "lab_id", "right", 400),
    ("labs", "lab_id", "right", "lab_quiz_questions", "lab_id", "left", 880),
    ("labs", "lab_id", "right", "lab_rubric_criteria", "lab_id", "left", 865),
    ("labs", "lab_id", "right", "lab_feedback_history", "lab_id", "left", 850),
]

# Drawn dashed and routed round the outside: a question may cite one learning
# objective, which is a second foreign key into a table already linked to labs.
# Keeping it dashed stops it reading as another lab_id edge.
OPTIONAL_RELATIONS = [
    ("lab_quiz_questions", "objective_id", "left",
     "lab_learning_objectives", "objective_id", "right", 410, 893),
]


def height(table: str) -> int:
    return HEADER_H + len(TABLES[table][3]) * ROW_H + PAD_BOTTOM


def row_y(table: str, column: str) -> int:
    x, y, _w, columns = TABLES[table]
    index = next(i for i, (name, _t, _pk) in enumerate(columns) if name == column)
    return y + HEADER_H + index * ROW_H - 8


def edge_x(table: str, side: str) -> int:
    x, _y, w, _c = TABLES[table]
    return x + w if side == "right" else x


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build() -> str:
    width = max(x + w for x, _y, w, _c in TABLES.values()) + 40
    canvas_height = max(y + height(name) for name, (_x, y, _w, _c) in TABLES.items()) + 90

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {canvas_height}" '
        f'width="{width}" height="{canvas_height}" font-family="{FONT}">',
        "<style>"
        ".box{fill:#fff;stroke:#000;stroke-width:2.5}"
        ".tname{font-size:19px;font-weight:700;text-anchor:middle}"
        ".col{font-size:15px}"
        ".pk{font-size:15px;font-style:italic}"
        ".type{font-size:11px;text-anchor:end;letter-spacing:.5px}"
        ".rel{fill:none;stroke:#000;stroke-width:2}"
        ".dash{stroke-dasharray:7 5}"
        ".sep{stroke:#000;stroke-width:2}"
        "</style>",
        f'<rect width="{width}" height="{canvas_height}" fill="#fff"/>',
    ]

    # Relationships first, so the boxes paint over the line ends.
    for src, src_col, src_side, dst, dst_col, dst_side, elbow in RELATIONS:
        x1, y1 = edge_x(src, src_side), row_y(src, src_col)
        x2, y2 = edge_x(dst, dst_side), row_y(dst, dst_col)
        parts.append(
            f'<path class="rel" d="M{x1},{y1} H{elbow} V{y2} H{x2}"/>'
        )

    for src, src_col, src_side, dst, dst_col, dst_side, elbow, exit_x in OPTIONAL_RELATIONS:
        x1, y1 = edge_x(src, src_side), row_y(src, src_col)
        x2, y2 = edge_x(dst, dst_side), row_y(dst, dst_col)
        floor = canvas_height - 36
        parts.append(
            f'<path class="rel dash" d="M{x1},{y1} H{exit_x} V{floor} H{elbow} V{y2} H{x2}"/>'
        )

    for name, (x, y, w, columns) in TABLES.items():
        h = height(name)
        parts.append(f'<rect class="box" x="{x}" y="{y}" width="{w}" height="{h}" rx="14"/>')
        parts.append(f'<text class="tname" x="{x + w / 2:.0f}" y="{y + 29}">{esc(name)}</text>')
        parts.append(f'<line class="sep" x1="{x}" y1="{y + 40}" x2="{x + w}" y2="{y + 40}"/>')

        for i, (column, column_type, is_pk) in enumerate(columns):
            ty = y + HEADER_H + i * ROW_H + 10
            cls = "pk" if is_pk else "col"
            parts.append(f'<text class="{cls}" x="{x + 14}" y="{ty}">{esc(column)}</text>')
            parts.append(f'<text class="type" x="{x + w - 14}" y="{ty}">{esc(column_type)}</text>')

    parts.append("</svg>")
    return "\n".join(parts)


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "docs" / "erd.svg"
    out.parent.mkdir(exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")
