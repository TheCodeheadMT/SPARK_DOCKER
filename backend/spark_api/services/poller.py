"""Watch the Blackboard mount and ingest new exports automatically.

Every BB_POLL_INTERVAL_S seconds:
  1. Probe the mount (with a timeout) and discover course folders.
  2. For each Blackboard course, take a cheap fingerprint (listing + stat, no download).
  3. If it differs from what was last ingested, wait until it has been unchanged for
     BB_SETTLE_S seconds (Blackboard may still be writing the file), then ingest.
     A fingerprint whose ingest failed is not retried until the files change again
     (or an admin forces it), so a bad export doesn't create a failed run every poll.

Watch statuses: up_to_date | waiting | ingesting | ingested | failed | no_gradebook |
                busy | mount_error

Only one process polls: with several gunicorn workers each one tries a file lock in
DATA_DIR and the holder polls. Run it outside the API with `python -m spark_api.poller`.
In Phase 5 this moves to the job worker.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from adapters.base import CourseRef
from spark_api.db import Course, Database, SourceWatch
from spark_api.services.ingest import IngestBusy, run_ingest
from spark_api.services.mount import MountIO, describe_error, probe

log = logging.getLogger("spark.poller")
POLLER_USER = "system:poller"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class LeaderLock:
    """Non-blocking exclusive file lock; whoever holds it is the poller."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        if self._fh:
            return True
        try:
            import fcntl
        except ImportError:          # Windows dev box: single process, no lock needed
            self._fh = True
            return True
        fh = open(self.path, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh not in (None, True):
            self._fh.close()
        self._fh = None


class Poller:
    def __init__(self, db: Database, adapter, data_dir: Path, discover, interval_s: int, settle_s: int,
                 io_timeout_s: int, require_mount: bool = False):
        self.require_mount = require_mount
        self.db, self.adapter, self.data_dir, self.discover = db, adapter, data_dir, discover
        self.interval_s, self.settle_s = interval_s, settle_s
        self.io = MountIO(io_timeout_s)
        self.lock = LeaderLock(data_dir / "poller.lock")
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_lock = threading.Lock()
        self.last_run: dict | None = None
        self.last_mount: dict | None = None

    # ---------------------------------------------------------------- one pass
    def run_once(self, force_course: str | None = None) -> dict:
        """One poll. force_course ignores settle time and failed-token memory for that course."""
        if not self._run_lock.acquire(blocking=False):
            return {"skipped": "a poll is already running"}
        try:
            return self._run(force_course)
        finally:
            self._run_lock.release()

    def _run(self, force_course: str | None) -> dict:
        t0 = time.monotonic()
        summary = {"started_at": _now().isoformat(), "checked": 0, "ingested": [], "failed": [], "waiting": []}
        st = probe(self.io, self.adapter, self.require_mount)
        self.last_mount = st.dict()
        if not st.reachable:
            summary["error"] = st.error
            self._mark_all("mount_error", st.error or "")
            return self._finish(summary, t0)
        if st.server_ok is False:
            summary["warning"] = st.error
        try:
            self.io.call(self.discover, "bb", timeout_s=self.io.timeout_s * 3)
        except Exception as exc:  # noqa: BLE001
            summary["error"] = describe_error(exc, st.mount_point)[0]
            return self._finish(summary, t0)
        with self.db.session() as s:
            courses = list(s.scalars(select(Course).where(Course.lms == "bb").order_by(Course.id)))
        for c in courses:
            summary["checked"] += 1
            outcome = self._check_course(c, force=(c.id == force_course))
            if outcome in ("ingested", "failed", "waiting"):
                summary[outcome].append(c.id)
        return self._finish(summary, t0)

    def _finish(self, summary: dict, t0: float) -> dict:
        summary["duration_s"] = round(time.monotonic() - t0, 2)
        self.last_run = summary
        if summary.get("ingested") or summary.get("failed") or summary.get("error") or summary.get("warning"):
            log.info("poll: %s", summary)
        return summary

    def _mark_all(self, status: str, detail: str) -> None:
        with self.db.session() as s:
            for w in s.scalars(select(SourceWatch)):
                w.status, w.detail, w.last_checked_at = status, detail, _now()
            s.commit()

    def _check_course(self, course: Course, force: bool) -> str:
        ref = CourseRef(course.id, course.name, course.lms, course.source)
        with self.db.session() as s:
            w = s.get(SourceWatch, course.id) or SourceWatch(course_id=course.id)
            w.last_checked_at = _now()
            try:
                fp = self.io.call(self.adapter.fingerprint, ref)
            except Exception as exc:  # noqa: BLE001
                w.status, w.detail = "mount_error", describe_error(exc, None)[0]
                s.merge(w)
                s.commit()
                return "mount_error"
            token, w.files = fp["token"], fp["files"]
            if token != w.seen_token:
                w.seen_token, w.seen_since = token, _now()

            if not fp["has_gradebook"]:
                w.status, w.detail = "no_gradebook", "No Full Gradebook export in the gradebook folder yet."
                outcome = "no_gradebook"
            elif token == w.ingested_token and not force:
                w.status, w.detail = "up_to_date", ""
                outcome = "up_to_date"
            elif token == w.failed_token and not force:
                w.status = "failed"
                outcome = "failed_known"
            else:
                newest = datetime.fromtimestamp(fp["newest_mtime"], timezone.utc) if fp["newest_mtime"] else _now()
                ready_at = max(_aware(w.seen_since), newest) + timedelta(seconds=self.settle_s)
                if not force and _now() < ready_at:
                    w.status = "waiting"
                    w.detail = f"New export seen; ingesting after it has been unchanged for {self.settle_s} s."
                    outcome = "waiting"
                else:
                    outcome = None
            s.merge(w)
            s.commit()
        if outcome is not None:
            return outcome if outcome != "failed_known" else "skipped"
        return self._ingest(course, ref, token, force)

    def _ingest(self, course: Course, ref: CourseRef, token: str, force: bool) -> str:
        self._set(course.id, status="ingesting", detail="")
        try:
            # Copy the exports locally under the mount timeout; run_ingest then reads the local copies.
            self.io.call(self.adapter.snapshot, ref, timeout_s=max(self.io.timeout_s * 15, 300))
            run = run_ingest(self.db, self.adapter, course, self.data_dir, POLLER_USER, force=False)
        except IngestBusy:
            self._set(course.id, status="busy", detail="Someone else is ingesting this course; retrying next poll.")
            return "waiting"
        except Exception as exc:  # noqa: BLE001
            msg, _ = describe_error(exc, None)
            # A file still being written or a mount hiccup is retried; anything else is remembered.
            transient = "changed while" in msg or "Mount" in msg or "did not respond" in msg
            self._set(course.id, status="waiting" if transient else "failed", detail=msg,
                      **({} if transient else {"failed_token": token}))
            return "waiting" if transient else "failed"
        if run.status in ("succeeded", "skipped"):
            self._set(course.id, status="ingested" if run.status == "succeeded" else "up_to_date",
                      detail=(run.report or {}).get("note", ""), ingested_token=token, failed_token="",
                      last_ingest_run_id=run.id)
            return "ingested" if run.status == "succeeded" else "up_to_date"
        self._set(course.id, status="failed", detail=run.error, failed_token=token, last_ingest_run_id=run.id)
        return "failed"

    def _set(self, course_id: str, **kw) -> None:
        with self.db.session() as s:
            w = s.get(SourceWatch, course_id) or SourceWatch(course_id=course_id)
            for k, v in kw.items():
                setattr(w, k, v)
            s.merge(w)
            s.commit()

    # ---------------------------------------------------------------- loop
    def start(self) -> None:
        if self.interval_s <= 0 or self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="bb-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.lock.release()

    def wake(self) -> None:
        self._wake.set()

    @property
    def is_leader(self) -> bool:
        return self.lock._fh is not None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.lock.acquire():
                try:
                    self.run_once()
                except Exception:  # noqa: BLE001  never let the loop die
                    log.exception("poll failed")
            self._wake.wait(self.interval_s)
            self._wake.clear()

    def status(self) -> dict:
        return {"enabled": self.interval_s > 0, "interval_s": self.interval_s, "settle_s": self.settle_s,
                "io_timeout_s": self.io.timeout_s, "leader": self.is_leader, "pid": os.getpid(),
                "last_run": self.last_run, "stuck_for_s": self.io.stuck_for_s}

    def watches(self) -> list[dict]:
        with self.db.session() as s:
            rows = s.scalars(select(SourceWatch).order_by(SourceWatch.course_id)).all()
            return [{"course_id": w.course_id, "status": w.status, "detail": w.detail, "files": w.files or {},
                     "seen_since": _aware(w.seen_since).isoformat() if w.seen_since else None,
                     "last_checked_at": _aware(w.last_checked_at).isoformat() if w.last_checked_at else None,
                     "last_ingest_run_id": w.last_ingest_run_id} for w in rows]


def main() -> None:
    """Standalone poller: python -m spark_api.poller [--once]"""
    import argparse

    from spark_api.config import get_settings
    from spark_api.main import create_app

    ap = argparse.ArgumentParser(description="Watch the Blackboard mount and ingest new exports.")
    ap.add_argument("--once", action="store_true", help="poll once and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    if not args.once and settings.bb_poll_interval_s <= 0:
        settings.bb_poll_interval_s = 300
    app = create_app(settings, start_poller=False)
    poller: Poller | None = app.state.poller
    if poller is None:
        raise SystemExit("BB_ROOT is not set; nothing to watch.")
    if args.once:
        print(poller.run_once())
        return
    poller.interval_s = settings.bb_poll_interval_s
    log.info("polling %s every %s s", poller.adapter.root, poller.interval_s)
    try:
        poller._loop()
    except KeyboardInterrupt:
        poller.stop()


if __name__ == "__main__":
    main()
