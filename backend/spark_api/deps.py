"""FastAPI dependencies: settings, DB, current user, course authorisation."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from spark_api.auth.base import SESSION_COOKIE, User
from spark_api.db import Course, CourseAccess, Database, HiddenCourse

ROLE_RANK = {"Instructor": 1, "CourseDirector": 2, "Admin": 3}


def get_db(request: Request) -> Database:
    return request.app.state.db


def current_user(request: Request) -> User:
    st = request.app.state
    # Test/dev convenience: header-based identity, never in production.
    sim_header = request.headers.get("X-Sim-User")
    if sim_header and st.settings.auth_mode in ("simulated", "local") and not st.settings.is_production:
        user = st.auth.user_for(sim_header) or st.auth.user_for_upn(sim_header)
        if user:
            return user
    token = request.cookies.get(SESSION_COOKIE)
    user = st.sessions.loads(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


@dataclass
class CourseContext:
    course: Course
    role: str              # effective role in this course
    user: User


def course_access(course_id: str, user: User = Depends(current_user), db: Database = Depends(get_db)) -> CourseContext:
    with db.session() as s:
        course = s.get(Course, course_id)
        if course is None:
            # Same response as "no access" so course IDs can't be probed.
            raise HTTPException(status_code=404, detail="Course not found")
        if user.is_admin:
            return CourseContext(course, "Admin", user)
        grant = s.scalars(select(CourseAccess).where(CourseAccess.upn == user.upn,
                                                     CourseAccess.course_id == course_id)).first()
        if not grant or s.get(HiddenCourse, (user.upn, course_id)) is not None:
            raise HTTPException(status_code=404, detail="Course not found")
        return CourseContext(course, grant.role, user)


def require_course_role(min_role: str):
    def _dep(ctx: CourseContext = Depends(course_access)) -> CourseContext:
        if ROLE_RANK.get(ctx.role, 0) < ROLE_RANK[min_role]:
            raise HTTPException(status_code=403, detail=f"Requires {min_role} role for this course")
        return ctx
    return _dep


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return user
