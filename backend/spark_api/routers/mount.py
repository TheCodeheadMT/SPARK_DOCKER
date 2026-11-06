"""Blackboard mount health and the automatic-ingest poller."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from spark_api.auth.base import User
from spark_api.db import AuditEvent, Database, SourceWatch
from spark_api.deps import CourseContext, course_access, get_db, require_admin
from spark_api.services.mount import probe

router = APIRouter(prefix="/api")


def _poller(request: Request):
    p = request.app.state.poller
    if p is None:
        raise HTTPException(status_code=404, detail="No Blackboard mount is configured (BB_ROOT).")
    return p


@router.get("/admin/mount")
async def mount_status(request: Request, user: User = Depends(require_admin)):
    p = request.app.state.poller
    if p is None:
        return {"mount": probe(None, None).dict(), "poller": None, "watches": []}
    st = await run_in_threadpool(probe, p.io, p.adapter, p.require_mount)
    return {"mount": st.dict(), "poller": p.status(), "watches": p.watches()}


@router.post("/admin/mount/poll")
async def poll_now(request: Request, course_id: str | None = None, user: User = Depends(require_admin),
                   db: Database = Depends(get_db)):
    """Poll now. With course_id, ingest that course even if it is settling or failed before."""
    p = _poller(request)
    summary = await run_in_threadpool(p.run_once, course_id)
    with db.session() as s:
        s.add(AuditEvent(upn=user.upn, action="mount_poll", course_id=course_id or "", target=""))
        s.commit()
    return {"summary": summary, "poller": p.status(), "watches": p.watches()}


@router.get("/courses/{course_id}/watch")
def course_watch(course_id: str, request: Request, ctx: CourseContext = Depends(course_access),
                 db: Database = Depends(get_db)):
    p = request.app.state.poller
    if ctx.course.lms != "bb" or p is None:
        return {"watched": False}
    with db.session() as s:
        w = s.get(SourceWatch, course_id)
    out = next((x for x in p.watches() if x["course_id"] == course_id), None) if w else None
    return {"watched": True, "auto": p.interval_s > 0, "interval_s": p.interval_s, "settle_s": p.settle_s,
            "watch": out}
