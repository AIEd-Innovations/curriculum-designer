"""Who is signed in, their courses, and the labs in one of them — issue #16.

These live at the top level, not under `/curriculum`: they are about the caller
and their courses, not about one lab's materials.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import current_user, owned_course
from models.instructor import (
    CourseOut,
    CreateCourseRequest,
    LabListResponse,
    LabSummary,
    MeResponse,
    UserOut,
)
from services.system import new_course

router = APIRouter(tags=["instructor"])


def _system_store(request: Request):
    system = getattr(request.app.state, "system_store", None)
    if system is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "NO_SYSTEM_STORE",
                    "message": "Users and courses are unavailable: no shared database configured.",
                    "agent": "curriculum-designer",
                }
            },
        )
    return system


@router.get("/me", response_model=MeResponse)
async def me(request: Request, user: dict = Depends(current_user)) -> MeResponse:
    """The signed-in instructor and the courses they own.

    First sign-in creates the user; a brand new instructor gets an empty
    course list, not an error.
    """
    system = _system_store(request)
    courses = system.list_courses(instructor_id=user["id"])
    return MeResponse(
        user=UserOut(id=user["id"], email=user["email"], name=user.get("name") or ""),
        courses=[CourseOut(id=c["id"], name=c["name"]) for c in courses],
    )


@router.post("/courses", response_model=CourseOut, status_code=201)
async def create_course(
    body: CreateCourseRequest, request: Request, user: dict = Depends(current_user),
) -> CourseOut:
    """Create a course owned by the caller. One owner per course."""
    name = body.name.strip()
    if not name:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "NAME_REQUIRED",
                    "message": "A course needs a name, for example 'CSC 580'.",
                    "agent": "curriculum-designer",
                }
            },
        )
    system = _system_store(request)
    course = new_course(name=name, instructor_id=user["id"], course_id=str(uuid4()))
    system.insert_course(course)
    return CourseOut(id=course["id"], name=course["name"])


@router.get("/courses/{course_id}/labs", response_model=LabListResponse)
async def list_course_labs(
    course_id: str, request: Request, user: dict = Depends(current_user),
) -> LabListResponse:
    """The labs in one of the caller's courses — what the dashboard lists."""
    owned_course(request, user, course_id)
    labs = request.app.state.store.list(course_id)
    return LabListResponse(
        course_id=course_id,
        labs=[
            LabSummary(
                lab_id=m.lab_id,
                course_id=m.course_id,
                title=m.title,
                approval_status=m.approval_status,
                spec_status=m.spec_status,
                quiz_status=m.quiz_status,
                rubric_status=m.rubric_status,
                version=m.version,
                last_updated=m.last_updated,
            )
            for m in sorted(labs, key=lambda m: m.last_updated, reverse=True)
        ],
    )
