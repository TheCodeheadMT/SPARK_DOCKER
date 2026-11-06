import pytest
from fastapi.testclient import TestClient

from spark_api.config import Settings
from spark_api.main import create_app
from tests.conftest import BB_COURSE, FIXTURES


@pytest.fixture()
def local(settings):
    settings.auth_mode = "local"
    return TestClient(create_app(settings, start_poller=False))


def login(c, who):
    r = c.post("/auth/sim/login", json={"persona": who})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def test_two_modes_no_credentials(local):
    cfg = local.get("/auth/config").json()
    assert cfg["mode"] == "local" and [p["key"] for p in cfg["personas"]] == ["instructor", "admin"]
    assert login(local, "instructor")["roles"] == ["Instructor"]
    assert login(local, "admin")["is_admin"] is True
    assert local.post("/auth/sim/login", json={"persona": "director"}).status_code == 400


def test_instructor_sees_every_course_including_new_ones(local):
    login(local, "instructor")
    ids = {c["id"] for c in local.get("/api/courses").json()["courses"]}
    assert {BB_COURSE, "fixture:DEMO001", "fixture:DEMO002"} <= ids
    # A course the admin adds by upload shows up for the instructor too.
    login(local, "admin")
    r = local.post("/api/uploads/courses", data={"dry_run": "false", "course_code": "202620-DEMO-CS999"},
                   files=[("files", ("gb.csv", (FIXTURES / "demo_gradebook.csv").read_bytes(), "text/csv"))]).json()
    assert r["ok"], r
    login(local, "instructor")
    ids = {c["id"] for c in local.get("/api/courses").json()["courses"]}
    assert "bb:202620-DEMO-CS999" in ids
    assert local.get("/api/admin/inventory").status_code == 403          # admin page stays admin-only


def test_local_mode_refused_in_production():
    with pytest.raises(RuntimeError, match="no sign-in"):
        Settings(env="production", auth_mode="local", secret_key="x").validate()
