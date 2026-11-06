from tests.conftest import BB_COURSE, as_user


def test_health(client):
    assert client.get("/api/health").json()["adapters"] == ["bb", "fixture"]


def test_requires_sign_in(client):
    assert client.get("/api/courses").status_code == 401


def test_simulated_login_sets_session_cookie(client):
    cfg = client.get("/auth/config").json()
    assert cfg["mode"] == "simulated" and {p["key"] for p in cfg["personas"]} >= {"instructor", "admin"}
    r = client.post("/auth/sim/login", json={"persona": "instructor"})
    assert r.status_code == 200 and "spark_session" in r.cookies
    me = client.get("/api/me").json()["user"]
    assert me["upn"] == "instructor.alpha@spark.test" and me["roles"] == ["Instructor"]
    client.post("/auth/logout")
    client.cookies.clear()
    assert client.get("/api/me").status_code == 401


def test_tampered_cookie_rejected(client):
    client.cookies.set("spark_session", "not-a-valid-token")
    assert client.get("/api/me").status_code == 401


def test_course_visibility_by_persona(client):
    ids = lambda p: {c["id"] for c in client.get("/api/courses", headers=as_user(p)).json()["courses"]}
    assert ids("instructor") == {BB_COURSE, "fixture:DEMO001"}
    assert ids("admin") == {BB_COURSE, "fixture:DEMO001", "fixture:DEMO002"}
    assert ids("noaccess") == set()


def test_no_access_looks_like_not_found(client):
    assert client.get(f"/api/courses/{BB_COURSE}", headers=as_user("noaccess")).status_code == 404
    assert client.get("/api/courses/fixture:DEMO002", headers=as_user("instructor")).status_code == 404


def test_ingest_then_roster_and_student(client):
    h = as_user("instructor")
    assert client.get(f"/api/courses/{BB_COURSE}/students", headers=h).status_code == 409   # no data yet
    run = client.post(f"/api/courses/{BB_COURSE}/ingest", headers=h).json()["run"]
    assert run["status"] == "succeeded" and run["report"]["students"] == 54
    again = client.post(f"/api/courses/{BB_COURSE}/ingest", headers=h).json()["run"]
    assert again["status"] == "skipped"

    detail = client.get(f"/api/courses/{BB_COURSE}", headers=h).json()
    assert [s["SECTION_NAME"] for s in detail["sections"]] == ["M3A", "T2A", "T3A"]

    roster = client.get(f"/api/courses/{BB_COURSE}/students", headers=h).json()["students"]
    assert len(roster) == 54
    sec = detail["sections"][0]["SECTION_ID"]
    sub = client.get(f"/api/courses/{BB_COURSE}/students", params={"section_id": sec}, headers=h).json()["students"]
    assert 0 < len(sub) < 54

    st = client.get(f"/api/courses/{BB_COURSE}/students/stu0005", headers=h).json()
    assert st["profile"]["SECTION_NAME"] == "M3A" and st["kpis"]["items_graded"] == 16
    fig = client.get(f"/api/courses/{BB_COURSE}/students/stu0005/figures/cumulative",
                     params={"cutoff": 70}, headers=h).json()
    assert fig["data"] and fig["layout"]
    assert client.get(f"/api/courses/{BB_COURSE}/students/nobody", headers=h).status_code == 404


def test_fixture_course_ingest(client):
    h = as_user("director")
    run = client.post("/api/courses/fixture:DEMO002/ingest", headers=h).json()["run"]
    assert run["status"] == "succeeded"
    assert len(client.get("/api/courses/fixture:DEMO002/students", headers=h).json()["students"]) == 60


def test_preferences_round_trip(client):
    h = as_user("instructor")
    assert client.get("/api/me/preferences", headers=h).json()["cutoff_percentage"] == 60
    client.put("/api/me/preferences", json={"cutoff_percentage": 72, "show_cutoff": True, "smooth_curve": False}, headers=h)
    assert client.get("/api/me/preferences", headers=h).json()["cutoff_percentage"] == 72


def test_audit_records_student_views(client, settings):
    from sqlalchemy import select
    from spark_api.db import AuditEvent, Database
    h = as_user("instructor")
    client.post(f"/api/courses/{BB_COURSE}/ingest", headers=h)
    client.get(f"/api/courses/{BB_COURSE}/students/stu0001", headers=h)
    db = Database(settings.resolved_database_url())
    with db.session() as s:
        ev = s.scalars(select(AuditEvent).where(AuditEvent.action == "view_student")).all()
    assert [(e.upn, e.target) for e in ev] == [("instructor.alpha@spark.test", "stu0001")]


def test_admin_only_discover(client):
    assert client.post("/api/admin/discover", headers=as_user("instructor")).status_code == 403
    assert client.post("/api/admin/discover", headers=as_user("admin")).json()["courses_found"] == 3
