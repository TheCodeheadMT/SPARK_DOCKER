import os
import shutil
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from adapters.base import CourseRef
from adapters.bb_ultra.adapter import BlackboardFileAdapter, ExportInProgress
from spark_api.db import IngestRun, SourceWatch
from spark_api.main import create_app
from spark_api.services.poller import LeaderLock
from tests.conftest import BB_COURSE, FIXTURES, as_user

COURSE_DIR = "202620-DEMO-CS101"


@pytest.fixture()
def app(settings):
    settings.bb_settle_s = 0
    settings.bb_io_timeout_s = 2
    return create_app(settings, start_poller=False)


def runs(app) -> list[IngestRun]:
    with app.state.db.session() as s:
        return list(s.query(IngestRun).filter(IngestRun.course_id == BB_COURSE).order_by(IngestRun.id))


def watch(app) -> SourceWatch:
    with app.state.db.session() as s:
        return s.get(SourceWatch, BB_COURSE)


def course_dir(bb_share):
    return bb_share / "SPARK" / COURSE_DIR


# ------------------------------------------------------------------ adapter
def test_fingerprint_reads_nothing_and_snapshot_copies_once(bb_share, tmp_path, monkeypatch):
    ad = BlackboardFileAdapter(bb_share, tmp_path / "snap")
    ref = ad.list_courses()[0]
    copies = []
    real = shutil.copyfile
    monkeypatch.setattr(shutil, "copyfile", lambda a, b: copies.append(a) or real(a, b))

    fp = ad.fingerprint(ref)
    assert fp["has_gradebook"] and fp["files"]["gradebook"]["name"] == "gb.csv" and copies == []
    v1 = ad.source_version(ref)
    assert len(copies) == 2                                   # gradebook + history, once each
    assert ad.source_version(ref) == v1 and len(copies) == 2  # reused, nothing re-read from the mount
    ad.load_course(ref)
    assert len(copies) == 2


def test_file_changing_during_copy_is_rejected(bb_share, tmp_path, monkeypatch):
    ad = BlackboardFileAdapter(bb_share, tmp_path / "snap")
    ref = ad.list_courses()[0]
    real = shutil.copyfile

    def growing(src, dst):
        real(src, dst)
        with open(src, "a") as f:                             # Blackboard still writing
            f.write("\n")
    monkeypatch.setattr(shutil, "copyfile", growing)
    with pytest.raises(ExportInProgress):
        ad.snapshot(ref)
    assert not list((tmp_path / "snap").rglob("*.part"))     # temp copy cleaned up


def test_snapshots_are_pruned(bb_share, tmp_path):
    ad = BlackboardFileAdapter(bb_share, tmp_path / "snap")
    ref = ad.list_courses()[0]
    gb = course_dir(bb_share) / "gradebook" / "gb.csv"
    for i in range(8):
        gb.write_bytes((FIXTURES / "demo_gradebook.csv").read_bytes() + f"\n# {i}".encode())
        ad.snapshot(ref)
    assert len(list((tmp_path / "snap").rglob("gradebook_*"))) == 5


# ------------------------------------------------------------------ poller
def test_poller_ingests_new_exports_and_then_idles(app, bb_share):
    p = app.state.poller
    s1 = p.run_once()
    assert BB_COURSE in s1["ingested"] and watch(app).status == "ingested"
    assert runs(app)[-1].triggered_by == "system:poller"
    n = len(runs(app))

    s2 = p.run_once()
    assert s2["ingested"] == [] and watch(app).status == "up_to_date" and len(runs(app)) == n

    # A new export lands (newer file in the same folder).
    new = course_dir(bb_share) / "gradebook" / "gb2.csv"
    new.write_bytes((FIXTURES / "demo_gradebook.csv").read_bytes())
    p.run_once()
    w = watch(app)
    assert w.files["gradebook"]["name"] == "gb2.csv" and w.ingested_token == w.seen_token
    # Identical content: the ingest recognises it and skips the rebuild.
    assert runs(app)[-1].status == "skipped" and w.status == "up_to_date"


def test_poller_waits_for_export_to_settle(app, bb_share):
    old = time.time() - 7200
    for f in course_dir(bb_share).rglob("*.csv"):
        os.utime(f, (old, old))
    p = app.state.poller
    p.settle_s = 3600
    assert BB_COURSE in p.run_once()["waiting"] and watch(app).status == "waiting"
    assert runs(app) == []
    with app.state.db.session() as s:                         # pretend an hour went by
        w = s.get(SourceWatch, BB_COURSE)
        w.seen_since = w.seen_since - timedelta(hours=2)
        s.commit()
    p.settle_s = 60                                           # file mtimes are older than this
    assert BB_COURSE in p.run_once()["ingested"]


def test_broken_export_fails_once_and_is_not_retried(app, bb_share):
    gb = course_dir(bb_share) / "gradebook" / "gb.csv"
    gb.write_text("this is not a gradebook\n1,2,3\n")
    p = app.state.poller
    assert BB_COURSE in p.run_once()["failed"] and watch(app).status == "failed"
    n = len(runs(app))
    p.run_once()
    assert len(runs(app)) == n                                # remembered, no new failed run
    # Admin "poll now" for the course forces a retry.
    c = TestClient(app)
    r = c.post(f"/api/admin/mount/poll?course_id={BB_COURSE}", headers=as_user("admin"))
    assert r.status_code == 200 and len(runs(app)) == n + 1


def test_hung_mount_times_out_instead_of_blocking(app, monkeypatch):
    p = app.state.poller
    p.io.timeout_s = 0.5
    monkeypatch.setattr(p.adapter, "list_courses", lambda: time.sleep(5) or [])
    t0 = time.monotonic()
    s = p.run_once()
    assert time.monotonic() - t0 < 3
    assert "did not respond" in s["error"] and p.last_mount["reachable"] is False
    assert "umount" in p.last_mount["hint"]


def test_missing_mount_is_reported(app, bb_share):
    shutil.rmtree(bb_share / "SPARK")
    s = app.state.poller.run_once()
    assert "does not exist" in s["error"]


def test_no_gradebook_yet(app, bb_share):
    (bb_share / "SPARK" / "NEWCOURSE" / "gradebook").mkdir(parents=True)
    app.state.poller.run_once()
    with app.state.db.session() as s:
        assert s.get(SourceWatch, "bb:NEWCOURSE").status == "no_gradebook"


def test_leader_lock_is_exclusive(tmp_path):
    a, b = LeaderLock(tmp_path / "l"), LeaderLock(tmp_path / "l")
    assert a.acquire() and not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


# ------------------------------------------------------------------ API
def test_mount_endpoints(app):
    c = TestClient(app)
    assert c.get("/api/admin/mount", headers=as_user("instructor")).status_code == 403
    j = c.get("/api/admin/mount", headers=as_user("admin")).json()
    assert j["mount"]["reachable"] and j["mount"]["courses"] == 1 and j["poller"]["enabled"] is False
    assert j["mount"]["is_mount"] is False                    # plain folder in tests
    c.post("/api/admin/mount/poll", headers=as_user("admin"))
    w = c.get(f"/api/courses/{BB_COURSE}/watch", headers=as_user("instructor")).json()
    assert w["watched"] and w["watch"]["status"] == "ingested"
    assert c.get(f"/api/courses/{BB_COURSE}/watch", headers=as_user("noaccess")).status_code == 404


def test_startup_survives_hung_mount(settings, monkeypatch):
    settings.bb_io_timeout_s = 1
    monkeypatch.setattr(BlackboardFileAdapter, "list_courses", lambda self: time.sleep(4) or [])
    t0 = time.monotonic()
    create_app(settings, start_poller=False)
    assert time.monotonic() - t0 < 3


def test_background_loop_runs_with_the_app(settings):
    settings.bb_settle_s, settings.bb_poll_interval_s = 0, 1
    app = create_app(settings)
    with TestClient(app):                                     # lifespan starts the poller thread
        for _ in range(50):
            if watch(app) and watch(app).status == "ingested":
                break
            time.sleep(0.1)
        assert watch(app).status == "ingested" and app.state.poller.is_leader
    assert not app.state.poller.is_leader                     # released on shutdown


def test_relative_root_is_not_reported_as_mount(settings, monkeypatch, bb_share):
    monkeypatch.chdir(bb_share.parent)
    settings.bb_root = bb_share.relative_to(bb_share.parent)
    app = create_app(settings, start_poller=False)
    j = TestClient(app).get("/api/admin/mount", headers=as_user("admin")).json()["mount"]
    assert j["reachable"] and j["is_mount"] is False


def test_require_mount_rejects_plain_folder(settings):
    settings.bb_require_mount = True
    app = create_app(settings, start_poller=False)
    s = app.state.poller.run_once()
    assert "is not mounted" in s["error"] and app.state.poller.last_mount["reachable"] is False
