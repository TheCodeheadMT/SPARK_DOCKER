"""SPARK BB Edition API.

Run (dev):  uvicorn spark_api.main:app --reload --port 8000   (from backend/)
Prod:       gunicorn -k uvicorn.workers.UvicornWorker -w 4 spark_api.main:app
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from adapters.bb_ultra.adapter import BlackboardFileAdapter
from adapters.canvas.adapter import CanvasAdapter
from adapters.fixture.adapter import FixtureAdapter
from spark_api.auth.base import SessionCodec
from spark_api.auth.local import LocalModeProvider
from spark_api.auth.simulated import SimulatedEntraProvider
from spark_api.config import Settings, get_settings
from spark_api.db import Course, CourseAccess, Database
from spark_api.routers import analytics as analytics_router
from spark_api.routers import auth as auth_router
from spark_api.routers import courses as courses_router
from spark_api.routers import data as data_router
from spark_api.routers import ei as ei_router
from spark_api.routers import mount as mount_router
from spark_api.routers import uploads as uploads_router
from spark_api.routers import removal as removal_router
from spark_api.services.llm import build_llm
from spark_api.services.poller import Poller

log = logging.getLogger("spark")


def build_adapters(settings: Settings) -> dict:
    adapters: dict = {}
    if settings.bb_root or settings.uploads_enabled:
        adapters["bb"] = BlackboardFileAdapter(
            settings.bb_root, settings.data_dir / "snapshots", total_column=settings.bb_total_column,
            letter_column=settings.bb_letter_column, late_grace_minutes=settings.late_grace_minutes,
            inbox_root=settings.data_dir / "inbox" if settings.uploads_enabled else None)
    if settings.enable_fixtures:
        adapters["fixture"] = FixtureAdapter()
    if settings.canvas_base_url and settings.canvas_api_token:
        adapters["canvas"] = CanvasAdapter(settings.canvas_base_url, settings.canvas_api_token)
    return adapters


def create_app(settings: Settings | None = None, start_poller: bool = True) -> FastAPI:
    settings = settings or get_settings()
    settings.validate()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        p = app.state.poller
        if start_poller and p is not None and settings.bb_poll_interval_s > 0:
            p.start()
        yield
        if p is not None:
            p.stop()

    app = FastAPI(title="SPARK BB Edition", version="0.1.0", lifespan=lifespan)
    st = app.state
    st.settings = settings
    st.db = Database(settings.resolved_database_url())
    st.db.create_all()
    st.sessions = SessionCodec(settings.secret_key, settings.session_max_age_s)
    st.adapters = build_adapters(settings)
    # uvicorn's logger is the one that prints at INFO in a terminal
    logging.getLogger("uvicorn.error").info("SPARK settings file: %s",
                                            settings.env_file or "none (environment variables and defaults)")
    st.llm = build_llm(settings)
    if settings.auth_mode == "local":
        st.auth = LocalModeProvider()
    elif settings.auth_mode == "simulated":
        st.auth = SimulatedEntraProvider(settings.sim_users_file)
        logging.getLogger("uvicorn.error").warning(
            "SPARK AUTH_MODE=simulated: the start page shows test personas. For just Instructor and Admin "
            "mode, set AUTH_MODE=local in %s (or remove the line).", settings.env_file or ".env")
    else:  # Phase 6
        raise RuntimeError("AUTH_MODE=entra is not implemented yet (Phase 6).")

    def discover_courses(only_lms: str | None = None) -> int:
        found = 0
        with st.db.session() as s:
            for lms, adapter in st.adapters.items():
                if only_lms and lms != only_lms:
                    continue
                try:
                    refs = adapter.list_courses()
                except Exception:
                    log.exception("course discovery failed for %s", lms)
                    continue
                for ref in refs:
                    found += 1
                    c = s.get(Course, ref.course_id)
                    if c is None:
                        s.add(Course(id=ref.course_id, name=ref.name, lms=ref.lms, source=ref.source))
                    else:
                        c.source = ref.source
            s.commit()
        seed_grants()
        return found

    def seed_grants() -> None:
        """Give the built-in users (local mode, simulated personas) their course access. "*" = every course."""
        if settings.auth_mode not in ("local", "simulated"):
            return
        with st.db.session() as s:
            known = set(s.scalars(select(Course.id)))
            have = {(a.upn, a.course_id) for a in s.scalars(select(CourseAccess))}
            for upn, cid, role in st.auth.course_grants():
                for c in (sorted(known) if cid == "*" else [cid]):
                    if c in known and (upn, c) not in have:
                        s.add(CourseAccess(upn=upn, course_id=c, role=role))
                        have.add((upn, c))
            s.commit()

    st.discover_courses = discover_courses
    st.seed_grants = seed_grants
    st.poller = None
    for lms in st.adapters:
        if lms != "bb":
            discover_courses(lms)
    if "bb" in st.adapters and settings.bb_root:
        st.poller = Poller(st.db, st.adapters["bb"], settings.data_dir, discover_courses,
                           settings.bb_poll_interval_s, settings.bb_settle_s, settings.bb_io_timeout_s,
                           settings.bb_require_mount)
        # The mount may be hung; never let it block startup.
        try:
            st.poller.io.call(discover_courses, "bb")
        except Exception as exc:  # noqa: BLE001
            log.warning("Blackboard course discovery skipped at startup: %s", exc)
    elif "bb" in st.adapters:
        discover_courses("bb")                       # uploads only: local folder, no timeout needed

    app.include_router(auth_router.router)
    app.include_router(courses_router.router)
    app.include_router(analytics_router.router)
    app.include_router(data_router.router)
    app.include_router(ei_router.router)
    app.include_router(mount_router.router)
    app.include_router(uploads_router.router)
    app.include_router(removal_router.router)

    @app.get("/api/health")
    def health():
        out = {"ok": True, "auth_mode": settings.auth_mode, "adapters": sorted(st.adapters),
               "ai": {"enabled": st.llm.enabled, "provider": st.llm.provider.name,
                      "disabled_reason": st.llm.disabled_reason}}
        if not settings.is_production:      # where settings came from; never the values of secrets
            out["env_file"] = settings.env_file
        return out

    if not settings.is_production:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    # Serve the built React app (frontend/dist) with SPA fallback.
    dist: Path = settings.frontend_dist
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            target = dist / full_path
            if full_path and target.is_file() and dist in target.resolve().parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


_app: FastAPI | None = None


def __getattr__(name: str):
    """Lazy `app` so `uvicorn spark_api.main:app` works without import-time side effects in tests."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
