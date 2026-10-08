"""
Uploaded-file models.

The file *bytes* live in the shared file storage (task 3). MongoDB holds the
metadata plus the text we extracted from the file, so that:

  * the instructor UI can list / delete attachments for a lab,
  * re-generation never has to re-parse a PDF,
  * `LabMaterial.material_content` can be rebuilt from its sources.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FileKind = Literal["material", "instructions"]


class LabFile(BaseModel):
    """One file an instructor attached to a lab."""

    file_id: str
    lab_id: str
    course_id: str = "csc580"

    filename: str
    content_type: str = "application/pdf"
    size_bytes: int
    kind: FileKind = "material"

    uploaded_by: str | None = None
    uploaded_at: datetime

    # Pointer into the shared file storage (task 3). Convention:
    #   labs/{course_id}/{lab_id}/{file_id}/{filename}
    # Nothing else in the schema depends on this string's shape.
    storage_key: str

    # Text extracted from this file, kept per-file rather than merged so a
    # single attachment can be removed without re-parsing the others.
    extracted_text: str = ""
    char_count: int = 0


class LabFileSummary(BaseModel):
    """What the instructor UI needs to render a file row — no text payload."""

    file_id: str
    filename: str
    content_type: str
    size_bytes: int
    kind: FileKind
    uploaded_by: str | None
    uploaded_at: datetime
    char_count: int

    @classmethod
    def from_file(cls, file: LabFile) -> "LabFileSummary":
        return cls(
            file_id=file.file_id,
            filename=file.filename,
            content_type=file.content_type,
            size_bytes=file.size_bytes,
            kind=file.kind,
            uploaded_by=file.uploaded_by,
            uploaded_at=file.uploaded_at,
            char_count=file.char_count,
        )


class LabFileListResponse(BaseModel):
    lab_id: str
    course_id: str
    files: list[LabFileSummary] = Field(default_factory=list)
    total_chars: int = 0


class DeleteFileResponse(BaseModel):
    file_id: str
    deleted: bool
    message: str


def build_storage_key(course_id: str, lab_id: str, file_id: str, filename: str) -> str:
    """Pointer handed to the shared file storage. See §3 of docs/data-model.md."""
    return f"labs/{course_id}/{lab_id}/{file_id}/{filename}"
