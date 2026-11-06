import pytest
from fastapi.testclient import TestClient

from spark_api.main import create_app
from tests.conftest import BB_COURSE, FIXTURES, as_user

GB = (FIXTURES / "demo_gradebook.csv").read_bytes()
HIST = (FIXTURES / "demo_grade_history.csv").read_bytes()
CODE = "202620-DEMO-CS101"
NEW = "bb:202620-DEMO-CS999"


def files(*pairs):
    return [("files", (n, d, "text/csv")) for n, d in pairs]


def ids(c, who):
    return [x["id"] for x in c.get("/api/courses", headers=as_user(who)).json()["courses"]]


@pytest.fixture()
def uploaded(settings):
    """Faculty Delta adds a course by upload, then trains a model and saves an EI session on it."""
    settings.bb_root = None
    settings.llm_provider = "fake"
    c = TestClient(create_app(settings, start_poller=False))
    H = as_user("noaccess")
    r = c.post("/api/uploads/courses", headers=H, data={"dry_run": "false", "course_code": NEW.split(":")[1]},
               files=files(("gb.csv", GB), ("h.csv", HIST))).json()
    assert r["run"]["status"] == "succeeded"
    assert c.post(f"/api/courses/{NEW}/models/train", headers=H,
                  json={"success_threshold": 85, "classifier": "rf"}).status_code == 200
    c.post(f"/api/courses/{NEW}/students/stu0005/ei/generate", headers=H, json={"kind": "plan", "objectives": "x"})
    return c


def test_hide_and_restore(client):
    H = as_user("instructor")
    assert BB_COURSE in ids(client, "instructor")
    info = client.get(f"/api/courses/{BB_COURSE}/removal", headers=H).json()
    assert info["can_hide"] and not info["can_delete"] and "other" in info["delete_blocked_reason"]
    assert client.post(f"/api/courses/{BB_COURSE}/hide", headers=H).status_code == 200
    assert BB_COURSE not in ids(client, "instructor")
    hidden = client.get("/api/courses", headers=H).json()["hidden_courses"]
    assert [h["id"] for h in hidden] == [BB_COURSE]
    assert client.get(f"/api/courses/{BB_COURSE}", headers=H).status_code == 404       # access suspended
    assert BB_COURSE in ids(client, "director")                                         # others unaffected
    # Re-running discovery (which re-seeds persona grants) doesn't bring it back.
    client.post("/api/admin/discover", headers=as_user("admin"))
    assert BB_COURSE not in ids(client, "instructor")
    assert client.post(f"/api/me/hidden-courses/{BB_COURSE}/restore", headers=H).status_code == 200
    assert BB_COURSE in ids(client, "instructor")
    assert client.post(f"/api/me/hidden-courses/{BB_COURSE}/restore", headers=H).status_code == 404


def test_mounted_course_cannot_be_deleted_even_by_admin(client):
    info = client.get(f"/api/courses/{BB_COURSE}/removal", headers=as_user("admin")).json()
    assert not info["can_delete"] and "Blackboard SPARK folder" in info["delete_blocked_reason"]
    r = client.post(f"/api/courses/{BB_COURSE}/delete", headers=as_user("admin"), json={"confirm": CODE})
    assert r.status_code == 403
    assert client.post(f"/api/courses/{BB_COURSE}/hide", headers=as_user("admin")).status_code == 400


def test_sole_owner_deletes_uploaded_course_everywhere(uploaded, settings):
    c, H = uploaded, as_user("noaccess")
    info = c.get(f"/api/courses/{NEW}/removal", headers=H).json()
    assert info["can_delete"] and info["impact"]["students"] == 54 and info["impact"]["success_models"] == 1
    assert info["impact"]["ei_sessions"] == 1
    # Wrong confirmation text is refused and nothing changes.
    assert c.post(f"/api/courses/{NEW}/delete", headers=H, json={"confirm": "yes"}).status_code == 400
    assert NEW in ids(c, "noaccess")
    r = c.post(f"/api/courses/{NEW}/delete", headers=H, json={"confirm": "202620-DEMO-CS999"}).json()
    assert set(r["removed"]) >= {"course_db", "snapshots", "models", "uploads"}
    assert NEW not in ids(c, "noaccess") and NEW not in ids(c, "admin")
    assert c.get(f"/api/courses/{NEW}", headers=as_user("admin")).status_code == 404
    leftovers = [p for p in settings.data_dir.rglob("*") if "CS999" in str(p)]
    assert leftovers == []
    # The audit trail stays.
    from spark_api.db import AuditEvent
    with c.app.state.db.session() as s:
        acts = {a.action for a in s.query(AuditEvent).filter(AuditEvent.course_id == NEW)}
    assert {"upload_exports", "delete_course"} <= acts
    # The same course can be added again afterwards.
    r = c.post("/api/uploads/courses", headers=H, data={"course_code": "202620-DEMO-CS999"},
               files=files(("gb.csv", GB))).json()
    assert r["ok"]


def test_cannot_delete_when_others_have_access(uploaded):
    c = uploaded
    from spark_api.db import CourseAccess
    with c.app.state.db.session() as s:
        s.add(CourseAccess(upn="instructor.alpha@spark.test", course_id=NEW, role="Instructor"))
        s.commit()
    info = c.get(f"/api/courses/{NEW}/removal", headers=as_user("noaccess")).json()
    assert not info["can_delete"] and info["others_with_access"] == 1
    assert c.post(f"/api/courses/{NEW}/delete", headers=as_user("noaccess"),
                  json={"confirm": "202620-DEMO-CS999"}).status_code == 403
    # An admin can.
    assert c.post(f"/api/courses/{NEW}/delete", headers=as_user("admin"),
                  json={"confirm": "202620-DEMO-CS999"}).status_code == 200


def test_deleting_a_course_whose_model_others_reuse(uploaded):
    c = uploaded
    assert c.put(f"/api/courses/fixture:DEMO001/models/alias", headers=as_user("admin"),
                 json={"source_course_id": NEW}).status_code in (200, 204)
    info = c.get(f"/api/courses/{NEW}/removal", headers=as_user("noaccess")).json()
    assert [x["id"] for x in info["impact"]["models_reused_by"]] == ["fixture:DEMO001"]
    c.post(f"/api/courses/{NEW}/delete", headers=as_user("noaccess"), json={"confirm": "202620-DEMO-CS999"})
    m = c.get("/api/courses/fixture:DEMO001/models", headers=as_user("admin")).json()
    assert m["alias_source"] is None


def test_no_access_gets_404(client):
    assert client.get(f"/api/courses/{BB_COURSE}/removal", headers=as_user("noaccess")).status_code == 404
    assert client.post(f"/api/courses/{BB_COURSE}/hide", headers=as_user("noaccess")).status_code == 404


def test_demo_courses_are_hidden_not_deleted(client):
    info = client.get("/api/courses/fixture:DEMO001/removal", headers=as_user("admin")).json()
    assert not info["can_delete"] and "ENABLE_FIXTURES" in info["delete_blocked_reason"]
    assert client.get("/api/courses/fixture:DEMO001/removal", headers=as_user("instructor")).json()["can_hide"]
