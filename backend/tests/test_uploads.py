import pytest
from fastapi.testclient import TestClient

from spark_api.main import create_app
from tests.conftest import BB_COURSE, FIXTURES, as_user

GB = (FIXTURES / "demo_gradebook.csv").read_bytes()
HIST = (FIXTURES / "demo_grade_history.csv").read_bytes()
CODE = "202620-DEMO-CS101"


def files(*pairs):
    return [("files", (name, data, "text/csv")) for name, data in pairs]


@pytest.fixture()
def uploads_only(settings):
    """No WebDAV mount at all: uploads are the only source."""
    settings.bb_root = None
    return TestClient(create_app(settings, start_poller=False))


@pytest.fixture()
def with_mount(client):
    return client


def inbox(settings):
    return settings.data_dir / "inbox" / "SPARK"


# ------------------------------------------------------------------ new course
def test_new_course_preview_detects_files_and_course_id(uploads_only):
    c = uploads_only
    # Deliberately "wrong" names: SPARK identifies the files by their columns.
    r = c.post("/api/uploads/courses", headers=as_user("noaccess"),
               files=files(("export (1).csv", HIST), ("export (2).csv", GB))).json()
    assert r["ok"] and r["errors"] == [] and r["course_code"] == CODE
    kinds = {f["filename"]: f["kind"] for f in r["files"]}
    assert kinds == {"export (1).csv": "grade_history", "export (2).csv": "gradebook"}
    gb = next(f for f in r["files"] if f["kind"] == "gradebook")
    assert gb["students"] == 54 and len(gb["sections"]) == 3
    assert c.get("/api/courses", headers=as_user("noaccess")).json()["courses"] == []   # dry run stored nothing


def test_new_course_commit_grants_uploader_and_ingests(uploads_only, settings):
    c = uploads_only
    assert c.get("/api/courses", headers=as_user("noaccess")).json()["can_add_courses"] is True
    r = c.post("/api/uploads/courses", headers=as_user("noaccess"),
               data={"dry_run": "false", "name": "CS 471 Spring"}, files=files(("gb.csv", GB), ("h.csv", HIST))).json()
    assert r["ok"] and r["course_id"] == BB_COURSE and r["run"]["status"] == "succeeded"
    courses = c.get("/api/courses", headers=as_user("noaccess")).json()["courses"]
    assert [(x["id"], x["name"], x["role"], x["has_data"]) for x in courses] == [(BB_COURSE, "CS 471 Spring", "Instructor", True)]
    assert len(c.get(f"/api/courses/{BB_COURSE}/students", headers=as_user("noaccess")).json()["students"]) == 54
    stored = sorted(p.name.split("_")[0] for p in (inbox(settings) / CODE).rglob("*.csv"))
    assert stored == ["grade", "gradebook"]                                       # SPARK-generated names
    # Adding the same course again is refused, without saying who has it.
    r2 = c.post("/api/uploads/courses", headers=as_user("instructor"), files=files(("gb.csv", GB))).json()
    assert not r2["ok"] and "can't be added" in r2["errors"][0]


def test_new_course_requires_gradebook_and_valid_id(uploads_only):
    c = uploads_only
    r = c.post("/api/uploads/courses", headers=as_user("noaccess"), files=files(("h.csv", HIST))).json()
    assert not r["ok"] and "must include the Full Gradebook" in r["errors"][0]
    r = c.post("/api/uploads/courses", headers=as_user("noaccess"), data={"course_code": "../etc"},
               files=files(("gb.csv", GB))).json()
    assert not r["ok"] and any("letters, digits" in e for e in r["errors"])


def test_bad_and_duplicate_files_are_rejected(uploads_only, settings):
    c = uploads_only
    r = c.post("/api/uploads/courses", headers=as_user("noaccess"), data={"dry_run": "false"},
               files=files(("notes.csv", b"a,b\n1,2\n"), ("gb.csv", GB))).json()
    assert not r["ok"] and "doesn't look like" in r["errors"][0]
    r = c.post("/api/uploads/courses", headers=as_user("noaccess"), data={"dry_run": "false"},
               files=files(("a.csv", GB), ("b.csv", GB))).json()
    assert not r["ok"] and "Two gradebook files" in r["errors"][0]
    r = c.post("/api/uploads/courses", headers=as_user("noaccess"), files=files(("x.pdf", b"%PDF")))
    assert not r.json()["ok"]
    assert not inbox(settings).exists() or not any(inbox(settings).rglob("*.*"))   # nothing stored


def test_gradebook_without_history_warns(uploads_only):
    r = uploads_only.post("/api/uploads/courses", headers=as_user("noaccess"), files=files(("gb.csv", GB))).json()
    assert r["ok"] and any("No Grade History" in w for w in r["warnings"])


# ------------------------------------------------------------------ existing course
def test_upload_newer_export_to_mounted_course(with_mount, settings):
    c = with_mount
    H = as_user("instructor")
    assert c.post(f"/api/courses/{BB_COURSE}/ingest", headers=H).json()["run"]["status"] == "succeeded"
    newer = GB.replace(b"Demo,Student0001", b"Demo,Student0001", 1) + b"\n"   # different bytes, same course
    r = c.post(f"/api/courses/{BB_COURSE}/uploads", headers=H, data={"dry_run": "false"},
               files=files(("gb.csv", newer))).json()
    assert r["ok"] and r["warnings"] == [] and r["run"]["status"] == "succeeded"
    used = r["run"]["report"]["files"]
    assert used["gradebook"].startswith("gradebook_") and used["grade_history"] == "gh.csv"   # upload + mount mixed


def test_upload_from_wrong_course_warns(with_mount):
    c, H = with_mount, as_user("instructor")
    c.post(f"/api/courses/{BB_COURSE}/ingest", headers=H)
    other = GB.replace(b"stu0", b"zzz0").replace(b"CS101", b"CS999")
    r = c.post(f"/api/courses/{BB_COURSE}/uploads", headers=H, files=files(("gb.csv", other))).json()
    assert r["ok"]                                                             # a warning, not a block
    assert any("belong to 202620-DEMO-CS999" in w for w in r["warnings"])
    assert any("students already in SPARK" in w for w in r["warnings"])
    r = c.post(f"/api/courses/{BB_COURSE}/uploads", headers=H, files=files(("gb.csv", GB), ("h.csv", other.replace(b"Last Name", b"x")))).json()
    assert not r["ok"]                                                         # second file isn't an export


def test_history_only_update_uses_current_gradebook(with_mount):
    c, H = with_mount, as_user("instructor")
    c.post(f"/api/courses/{BB_COURSE}/ingest", headers=H)
    r = c.post(f"/api/courses/{BB_COURSE}/uploads", headers=H, data={"dry_run": "false"},
               files=files(("h.csv", HIST + b"\n"))).json()
    assert r["ok"] and r["run"]["status"] == "succeeded"


def test_upload_access_rules(with_mount, settings):
    c = with_mount
    assert c.post(f"/api/courses/{BB_COURSE}/uploads", headers=as_user("noaccess"),
                  files=files(("gb.csv", GB))).status_code == 404
    assert c.post("/api/courses/fixture:DEMO001/uploads", headers=as_user("instructor"),
                  files=files(("gb.csv", GB))).status_code == 400
    settings.uploads_enabled = False
    c2 = TestClient(create_app(settings, start_poller=False))
    assert c2.post(f"/api/courses/{BB_COURSE}/uploads", headers=as_user("instructor"),
                   files=files(("gb.csv", GB))).status_code == 404
    assert c2.get("/api/courses", headers=as_user("instructor")).json()["can_add_courses"] is False


def test_only_last_five_uploads_are_kept(with_mount, settings):
    c, H = with_mount, as_user("instructor")
    for i in range(7):
        c.post(f"/api/courses/{BB_COURSE}/uploads", headers=H, data={"dry_run": "false"},
               files=files(("gb.csv", GB + f"\n{' ' * i}".encode())))
    assert len(list((inbox(settings) / CODE / "gradebook").iterdir())) == 5


def test_upload_size_limit_and_cleanup(uploads_only, settings):
    c = uploads_only
    assert c.get("/api/courses", headers=as_user("noaccess")).json()["upload_max_mb"] == 200
    c.app.state.settings.upload_max_mb = 1
    big = GB + b"\n" * (1024 * 1024)
    r = c.post("/api/uploads/courses", headers=as_user("noaccess"), files=files(("gb.csv", big)))
    assert r.status_code == 413 and "1 MB max" in r.json()["detail"]
    r = c.post("/api/uploads/courses", headers=as_user("noaccess"), files=files(("gb.csv", GB)))
    assert r.status_code == 200
    assert list((settings.data_dir / "tmp").iterdir()) == []          # scratch files always removed
