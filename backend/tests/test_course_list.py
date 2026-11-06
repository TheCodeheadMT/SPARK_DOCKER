from tests.conftest import BB_COURSE, as_user

H = as_user("director")          # director sees three courses: the Bb course and both demos


def listing(c, who=H):
    return c.get("/api/courses", headers=who).json()


def by_id(j):
    return {x["id"]: x for x in j["courses"]}


def test_opening_a_course_records_last_opened(client):
    assert all(x["last_opened_at"] is None for x in listing(client)["courses"])
    assert listing(client)["course_sort"] == "recent"
    client.get("/api/courses/fixture:DEMO002", headers=H)
    d = by_id(listing(client))
    assert d["fixture:DEMO002"]["last_opened_at"] and d[BB_COURSE]["last_opened_at"] is None
    # Per user: someone else's list is unaffected.
    assert by_id(listing(client, as_user("admin")))["fixture:DEMO002"]["last_opened_at"] is None


def test_custom_order_saved_and_switches_mode(client):
    order = ["fixture:DEMO002", BB_COURSE, "fixture:DEMO001", "fixture:NOT-MINE", "fixture:DEMO002"]
    r = client.put("/api/me/course-order", headers=H, json={"course_ids": order}).json()
    assert r["order"] == ["fixture:DEMO002", BB_COURSE, "fixture:DEMO001"]          # deduped, unknown dropped
    j = listing(client)
    assert j["course_sort"] == "custom"
    assert {x["id"]: x["position"] for x in j["courses"]} == {"fixture:DEMO002": 0, BB_COURSE: 1, "fixture:DEMO001": 2}
    # Instructor can't place a course they can't see.
    r = client.put("/api/me/course-order", headers=as_user("instructor"), json={"course_ids": ["fixture:DEMO002"]})
    assert r.json()["order"] == []


def test_sort_mode_and_cutoff_preferences_coexist(client):
    assert client.put("/api/me/course-sort", headers=H, json={"mode": "name"}).status_code == 200
    client.put("/api/me/preferences", headers=H, json={"cutoff_percentage": 70, "show_cutoff": True, "smooth_curve": False})
    assert listing(client)["course_sort"] == "name"                                   # not wiped by the prefs save
    assert client.get("/api/me/preferences", headers=H).json()["cutoff_percentage"] == 70
    assert client.put("/api/me/course-sort", headers=H, json={"mode": "bogus"}).status_code == 422


def test_archive_is_per_user_and_course_still_opens(client):
    assert client.post(f"/api/courses/{BB_COURSE}/archive", headers=H, json={"archived": True}).status_code == 200
    d = by_id(listing(client))
    assert d[BB_COURSE]["archived"] and d[BB_COURSE]["archived_at"]
    assert client.get(f"/api/courses/{BB_COURSE}", headers=H).status_code == 200        # still opens
    assert not by_id(listing(client, as_user("instructor")))[BB_COURSE]["archived"]     # others unaffected
    client.post(f"/api/courses/{BB_COURSE}/archive", headers=H, json={"archived": False})
    assert not by_id(listing(client))[BB_COURSE]["archived"]
    assert client.post(f"/api/courses/{BB_COURSE}/archive", headers=as_user("noaccess"),
                       json={"archived": True}).status_code == 404
