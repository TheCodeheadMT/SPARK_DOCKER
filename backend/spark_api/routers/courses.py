from __future__ import annotations

import json
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from spark_api.auth.base import User
from spark_api.db import (AuditEvent, Course, CourseAccess, CoursePref, Database, HiddenCourse, IngestRun,
                          UserPreference, utcnow)
from spark_api.deps import CourseContext, course_access, current_user, get_db, require_admin, require_course_role
from spark_api.services import queries
from spark_api.services.ingest import IngestBusy, run_ingest
from spark_core.etl.store import course_db_path
from spark_core.plotting import FIGURES, PlotSettings

router = APIRouter(prefix="/api")


def _run_summary(run: IngestRun | None) -> dict | None:
    if not run:
        return None
    return {"id": run.id, "status": run.status, "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "source_version": run.source_version, "triggered_by": run.triggered_by,
            "report": run.report or {}, "error": run.error}


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _pref(s, upn: str, course_id: str) -> CoursePref:
    p = s.get(CoursePref, (upn, course_id))
    if p is None:
        p = CoursePref(upn=upn, course_id=course_id)
        s.add(p)
    return p


def _latest_run(s, course_id: str, status: str | None = None) -> IngestRun | None:
    q = select(IngestRun).where(IngestRun.course_id == course_id)
    if status:
        q = q.where(IngestRun.status == status)
    return s.scalars(q.order_by(IngestRun.id.desc()).limit(1)).first()


def _db_file(request: Request, ctx: CourseContext):
    f = course_db_path(request.app.state.settings.data_dir, ctx.course.id)
    if not f.exists():
        raise HTTPException(status_code=409, detail="This course has no data yet. Run an ingest first.")
    return f


def _audit(db: Database, user: User, action: str, course_id: str = "", target: str = "") -> None:
    with db.session() as s:
        s.add(AuditEvent(upn=user.upn, action=action, course_id=course_id, target=target))
        s.commit()


# ---------------------------------------------------------------- courses
@router.get("/courses")
def list_courses(request: Request, user: User = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        if user.is_admin:
            rows = [(c, "Admin") for c in s.scalars(select(Course).order_by(Course.name))]
        else:
            rows = s.execute(select(Course, CourseAccess.role).join(CourseAccess, CourseAccess.course_id == Course.id)
                             .where(CourseAccess.upn == user.upn).order_by(Course.name)).all()
        hidden_ids = set(s.scalars(select(HiddenCourse.course_id).where(HiddenCourse.upn == user.upn)))
        hidden = [{"id": c.id, "name": c.name, "lms": c.lms} for c, _ in rows if c.id in hidden_ids]
        rows = [(c, role) for c, role in rows if c.id not in hidden_ids]
        prefs = {p.course_id: p for p in s.scalars(select(CoursePref).where(CoursePref.upn == user.upn))}
        up = s.get(UserPreference, user.upn)
        course_sort = ((up.prefs or {}) if up else {}).get("course_sort", "recent")
        out = []
        for c, role in rows:
            has_data = course_db_path(request.app.state.settings.data_dir, c.id).exists()
            p = prefs.get(c.id)
            out.append({"id": c.id, "name": c.name, "lms": c.lms, "role": role, "has_data": has_data,
                        "last_run": _run_summary(_latest_run(s, c.id)),
                        "last_success": _run_summary(_latest_run(s, c.id, "succeeded")),
                        "last_opened_at": _iso(p.last_opened_at) if p else None,
                        "position": p.position if p else None,
                        "archived": bool(p and p.archived),
                        "archived_at": _iso(p.archived_at) if p else None})
    st = request.app.state
    can_add = (st.settings.uploads_enabled and "bb" in st.adapters
               and bool(set(user.roles) & {"Instructor", "CourseDirector", "Admin"}))
    return {"courses": out, "can_add_courses": can_add, "hidden_courses": hidden,
            "upload_max_mb": st.settings.upload_max_mb, "course_sort": course_sort}


@router.get("/courses/{course_id}")
def course_detail(course_id: str, request: Request, ctx: CourseContext = Depends(course_access),
                  db: Database = Depends(get_db)):
    with db.session() as s:
        last = _run_summary(_latest_run(s, course_id, "succeeded"))
        # "Recently opened" ordering on the course list; at most one write a minute per user and course.
        p = _pref(s, ctx.user.upn, course_id)
        seen = p.last_opened_at
        if seen is not None and seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        if seen is None or (utcnow() - seen).total_seconds() > 60:
            p.last_opened_at = utcnow()
            s.commit()
    f = course_db_path(request.app.state.settings.data_dir, course_id)
    secs = queries.records(queries.sections(f)) if f.exists() else []
    return {"id": ctx.course.id, "name": ctx.course.name, "lms": ctx.course.lms, "role": ctx.role,
            "has_data": f.exists(), "last_success": last, "sections": secs}


@router.post("/courses/{course_id}/ingest")
async def ingest(course_id: str, request: Request, force: bool = False,
                 ctx: CourseContext = Depends(require_course_role("Instructor")), db: Database = Depends(get_db)):
    adapter = request.app.state.adapters.get(ctx.course.lms)
    if adapter is None:
        raise HTTPException(status_code=400, detail=f"No adapter configured for LMS '{ctx.course.lms}'")
    try:
        run = await run_in_threadpool(run_ingest, db, adapter, ctx.course, request.app.state.settings.data_dir,
                                      ctx.user.upn, force)
    except IngestBusy:
        raise HTTPException(status_code=409, detail="An ingest for this course is already running")
    return {"run": _run_summary(run)}


@router.get("/courses/{course_id}/ingest-runs")
def ingest_runs(course_id: str, ctx: CourseContext = Depends(course_access), db: Database = Depends(get_db)):
    with db.session() as s:
        runs = s.scalars(select(IngestRun).where(IngestRun.course_id == course_id)
                         .order_by(IngestRun.id.desc()).limit(25)).all()
        return {"runs": [_run_summary(r) for r in runs]}


# ---------------------------------------------------------------- students
@router.get("/courses/{course_id}/students")
def students(course_id: str, request: Request, section_id: str | None = None,
             ctx: CourseContext = Depends(course_access), db: Database = Depends(get_db)):
    df = queries.roster(_db_file(request, ctx), section_id)
    from spark_api.services.models import predictions
    probs, model = predictions(db, request.app.state.settings.data_dir, course_id)
    df["success_prob"] = df["STUDENT_ID"].map(probs) if probs is not None else None
    _audit(db, ctx.user, "view_roster", course_id, section_id or "")
    return {"students": queries.records(df), "model": model}


@router.get("/courses/{course_id}/students/{student_id}")
def student(course_id: str, student_id: str, request: Request,
            ctx: CourseContext = Depends(course_access), db: Database = Depends(get_db)):
    f = _db_file(request, ctx)
    prof = queries.student_profile(f, student_id)
    if prof is None:
        raise HTTPException(status_code=404, detail="Student not in this course")
    rows = queries.student_rows(f, student_id)
    from spark_api.services.models import predictions
    probs, model = predictions(db, request.app.state.settings.data_dir, course_id)
    prof["kpis"]["success_prob"] = float(probs[student_id]) if probs is not None and student_id in probs.index else None
    prof["model"] = model
    _audit(db, ctx.user, "view_student", course_id, student_id)
    cols = ["ASSIGNMENT_ID", "ASSIGNMENT", "ASSIGNMENT_GROUP_ID", "DUE_AT", "DUE_SOURCE", "SUBMITTED_AT",
            "GRADED_AT", "POINTS_EARNED", "POINTS_POSSIBLE", "PERCENTAGE", "LATE", "MISSED_ASSIGNMENT",
            "SUBMISSION_DELTA_DAYS"]
    return {**prof, "assignments": queries.records(rows[cols])}


@router.get("/courses/{course_id}/students/{student_id}/figures/{name}")
def student_figure(course_id: str, student_id: str, name: str, request: Request,
                   cutoff: float = Query(60.0, ge=0, le=100), show_cutoff: bool = True,
                   smooth: bool = False, theme: str = Query("light", pattern="^(light|dark)$"),
                   ctx: CourseContext = Depends(course_access)):
    fn = FIGURES.get(name)
    if fn is None:
        raise HTTPException(status_code=404, detail="Unknown figure")
    rows = queries.student_rows(_db_file(request, ctx), student_id)
    if rows.empty:
        raise HTTPException(status_code=404, detail="No data for student")
    fig = fn(rows, PlotSettings(cutoff_percentage=cutoff, show_cutoff=show_cutoff, smooth_curve=smooth, theme=theme))
    return Response(content=fig.to_json(), media_type="application/json")


# ---------------------------------------------------------------- course list arrangement
SORT_MODES = ("recent", "custom", "name", "updated")


class CourseOrder(BaseModel):
    course_ids: list[str] = Field(..., max_length=2000)


@router.put("/me/course-order")
def set_course_order(body: CourseOrder, user: User = Depends(current_user), db: Database = Depends(get_db)):
    """Save the user's manual order and switch their list to it. Unknown or inaccessible IDs are ignored."""
    with db.session() as s:
        if user.is_admin:
            allowed = set(s.scalars(select(Course.id)))
        else:
            allowed = set(s.scalars(select(CourseAccess.course_id).where(CourseAccess.upn == user.upn)))
        ids = [c for c in dict.fromkeys(body.course_ids) if c in allowed]
        for p in s.scalars(select(CoursePref).where(CoursePref.upn == user.upn)):
            p.position = None
        for i, cid in enumerate(ids):
            _pref(s, user.upn, cid).position = i
        up = s.get(UserPreference, user.upn) or UserPreference(upn=user.upn, prefs={})
        up.prefs = {**(up.prefs or {}), "course_sort": "custom"}
        s.merge(up)
        s.commit()
    return {"order": ids, "course_sort": "custom"}


class CourseSort(BaseModel):
    mode: str = Field(..., pattern="^(recent|custom|name|updated)$")


@router.put("/me/course-sort")
def set_course_sort(body: CourseSort, user: User = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        up = s.get(UserPreference, user.upn) or UserPreference(upn=user.upn, prefs={})
        up.prefs = {**(up.prefs or {}), "course_sort": body.mode}
        s.merge(up)
        s.commit()
    return {"course_sort": body.mode}


class ArchiveBody(BaseModel):
    archived: bool = True


@router.post("/courses/{course_id}/archive")
def archive_course(course_id: str, body: ArchiveBody, ctx: CourseContext = Depends(course_access),
                   db: Database = Depends(get_db)):
    """Archive or unarchive a course in this user's list. Others are not affected; the course still opens."""
    with db.session() as s:
        p = _pref(s, ctx.user.upn, course_id)
        p.archived = body.archived
        p.archived_at = utcnow() if body.archived else None
        s.commit()
    return {"course_id": course_id, "archived": body.archived}


# ---------------------------------------------------------------- preferences
class Preferences(BaseModel):
    cutoff_percentage: float = Field(60.0, ge=0, le=100)
    show_cutoff: bool = True
    smooth_curve: bool = False


@router.get("/me/preferences")
def get_prefs(user: User = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        p = s.get(UserPreference, user.upn)
        return Preferences(**(p.prefs if p else {})).model_dump()


@router.put("/me/preferences")
def put_prefs(body: Preferences, user: User = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        p = s.get(UserPreference, user.upn) or UserPreference(upn=user.upn, prefs={})
        p.prefs = {**(p.prefs or {}), **json.loads(body.model_dump_json())}     # keep other keys (course_sort)
        s.merge(p)
        s.commit()
    return body.model_dump()


# ---------------------------------------------------------------- admin
@router.post("/admin/discover")
async def discover(request: Request, user: User = Depends(require_admin)):
    st = request.app.state
    n = 0
    for lms in st.adapters:
        if lms == "bb" and st.poller is not None:   # the mount may hang: use its timeout
            try:
                n += await run_in_threadpool(st.poller.io.call, st.discover_courses, "bb")
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=504, detail=f"Blackboard mount: {exc}")
        else:
            n += await run_in_threadpool(st.discover_courses, lms)
    return {"courses_found": n}
