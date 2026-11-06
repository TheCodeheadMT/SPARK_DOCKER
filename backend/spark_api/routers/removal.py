"""Remove a course from your list, restore it, or delete it from SPARK."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from spark_api.auth.base import User
from spark_api.db import AuditEvent, Course, Database
from spark_api.deps import CourseContext, course_access, current_user, get_db
from spark_api.services import removal as R
from spark_api.services.ingest import _locks

router = APIRouter(prefix="/api")


def _audit(db: Database, upn: str, action: str, course_id: str, target: str = "") -> None:
    with db.session() as s:
        s.add(AuditEvent(upn=upn, action=action, course_id=course_id, target=target))
        s.commit()


def _ctx_impact(request: Request, ctx: CourseContext, db: Database) -> dict:
    st = request.app.state
    adapter = st.adapters.get("bb")
    poller = st.poller

    def mount_check(course):            # the mount may hang; use the poller's timeout when there is one
        if poller is None:
            return R.on_mount(adapter, course)
        try:
            return poller.io.call(R.on_mount, adapter, course)
        except Exception:  # noqa: BLE001
            return True
    return R.impact(db, st.settings.data_dir, ctx.course, ctx.user.upn, ctx.user.is_admin, adapter, mount_check)


@router.get("/courses/{course_id}/removal")
async def removal_options(course_id: str, request: Request, ctx: CourseContext = Depends(course_access),
                          db: Database = Depends(get_db)):
    """What removing this course would do, for the confirmation dialog."""
    return await run_in_threadpool(_ctx_impact, request, ctx, db)


@router.post("/courses/{course_id}/hide")
def hide_course(course_id: str, ctx: CourseContext = Depends(course_access), db: Database = Depends(get_db)):
    if ctx.user.is_admin:
        raise HTTPException(status_code=400, detail="Administrators see every course; use Delete instead.")
    R.hide(db, ctx.user.upn, course_id)
    _audit(db, ctx.user.upn, "hide_course", course_id)
    return {"hidden": course_id}


@router.post("/me/hidden-courses/{course_id}/restore")
def restore_course(course_id: str, user: User = Depends(current_user), db: Database = Depends(get_db)):
    if not R.restore(db, user.upn, course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    _audit(db, user.upn, "restore_course", course_id)
    return {"restored": course_id}


class DeleteBody(BaseModel):
    confirm: str                       # must equal the course ID (the part after "bb:")


@router.post("/courses/{course_id}/delete")
async def delete_course(course_id: str, body: DeleteBody, request: Request,
                        ctx: CourseContext = Depends(course_access), db: Database = Depends(get_db)):
    info = await run_in_threadpool(_ctx_impact, request, ctx, db)
    if not info["can_delete"]:
        raise HTTPException(status_code=403, detail=info["delete_blocked_reason"])
    if body.confirm.strip() != info["course"]["code"]:
        raise HTTPException(status_code=400, detail="Type the course ID exactly to confirm.")
    lock = _locks[course_id]
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="The course is being updated right now. Try again in a minute.")
    try:
        adapter = request.app.state.adapters.get("bb")
        with db.session() as s:
            course = s.get(Course, course_id)
        removed = await run_in_threadpool(R.delete_course, db, request.app.state.settings.data_dir, course,
                                          getattr(adapter, "inbox", None))
    finally:
        lock.release()
    _audit(db, ctx.user.upn, "delete_course", course_id, ",".join(removed))
    return {"deleted": course_id, "removed": removed}
