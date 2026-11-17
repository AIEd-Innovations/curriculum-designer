"""Who is signed in, and what they own — the shapes `GET /me` and friends return.

Issue #16: the Curriculum Designer answers "who am I and what are my courses",
and the instructor dashboard (ui-instructor#30) and the assessment agent
(assessment_agent#23) both read it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class UserOut(BaseModel):
    id: str
    email: str
    name: str = ""


class CourseOut(BaseModel):
    id: str
    name: str


class MeResponse(BaseModel):
    user: UserOut
    courses: list[CourseOut]


class CreateCourseRequest(BaseModel):
    name: str


class LabSummary(BaseModel):
    """One row of the dashboard's lab list — metadata only, no spec or quiz."""
    lab_id: str
    course_id: str
    title: str
    approval_status: str
    spec_status: str
    quiz_status: str
    rubric_status: str
    version: int
    last_updated: datetime


class LabListResponse(BaseModel):
    course_id: str
    labs: list[LabSummary]
