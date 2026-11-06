import json
from datetime import datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from spark_api.main import create_app
from spark_core.ei import signals as S
from spark_core.ei.export import markdown_to_html, markdown_to_pdf_bytes
from spark_core.ei.redact import redact
from tests.conftest import BB_COURSE, as_user

H = as_user("instructor")
STU = "stu0005"
BASE = f"/api/courses/{BB_COURSE}/students/{STU}/ei"


@pytest.fixture()
def fake_client(settings):
    settings.llm_provider = "fake"
    c = TestClient(create_app(settings))
    assert c.post(f"/api/courses/{BB_COURSE}/ingest", headers=H).json()["run"]["status"] == "succeeded"
    return c


@pytest.fixture()
def nollm_client(client):
    assert client.post(f"/api/courses/{BB_COURSE}/ingest", headers=H).json()["run"]["status"] == "succeeded"
    return client


def sse(text: str) -> list[tuple[str, object]]:
    out = []
    for block in text.strip().split("\n\n"):
        ev = dt = None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                dt = json.loads(line[6:])
        out.append((ev, dt))
    return out


# ------------------------------------------------------------------ core logic
def perf(rows):
    return pd.DataFrame(rows, columns=["ASSIGNMENT", "POINTS_EARNED", "POINTS_POSSIBLE", "SUBMITTED_AT", "DUE_AT",
                                       "LATE", "MISSED_ASSIGNMENT"])


def test_signals_use_missed_and_timed_items_only():
    t = pd.Timestamp
    df = perf([
        ("HW1", 10, 10, t("2026-01-01 10:00"), t("2026-01-02 23:59"), False, False),
        ("HW2", 0, 10, None, t("2026-01-09 23:59"), False, True),          # missed
        ("Exam", 40, 50, None, None, False, False),                        # hand-graded, no submit time
        ("HW3", 9, 10, t("2026-01-16 23:00"), t("2026-01-16 23:59"), True, False),
    ])
    s = S.compute_signals(df, datetime(2026, 1, 26, 23, 0))
    assert s["missing_rate"] == 0.25 and s["missing_count"] == 1
    assert s["late_rate"] == 0.5 and s["timed_items"] == 2                 # exam not counted as on time
    assert s["near_due_rate"] == 0.5
    assert round(s["days_since_last_submission"]) == 10                    # to the snapshot, not today
    assert round(s["current_grade_pct"], 1) == round(59 / 80 * 100, 1)


def test_needs_intervention_handles_no_grade_and_exemption():
    flag, reasons = S.needs_intervention({"current_grade_pct": None}, None, None)   # original crashed here
    assert flag and "No graded work yet" in reasons
    flag, _ = S.needs_intervention({"current_grade_pct": 90, "missing_rate": 0.5}, "Critical", 10)
    assert not flag
    flag, reasons = S.needs_intervention({"current_grade_pct": 65, "late_rate": 0.25}, "Low", 80)
    assert flag and any("Late rate" in r for r in reasons) and any("grade is low" in r for r in reasons)


def test_recommend_modules_count_and_note_keywords():
    sig = {"late_rate": 0.0, "missing_rate": 0.0, "avg_pct": 90, "std_pct": 5, "low_score_count": 0}
    assert [m["key"] for m in S.recommend_modules(sig, "Low")] == ["active_study_cycle"]
    recs = S.recommend_modules({**sig, "missing_rate": 0.2, "avg_pct": 60}, "High", "worried about the exam")
    assert len(recs) == 3 and recs[0]["key"] == "time_task_triage"
    assert any(m["key"] == "exam_readiness" for m in
               S.recommend_modules(sig, "Medium", "", "I panic on every quiz"))


def test_redact_names_ids_and_email():
    txt = "Talked to Jane Doe (jdoe, 1234567). Doe, Jane said JANE will email jane.doe@school.edu."
    out, n = redact(txt, "Doe, Jane", ["jdoe", "1234567"])
    assert "Jane" not in out and "Doe" not in out and "jdoe" not in out and "1234567" not in out
    assert "@" not in out and n >= 6
    assert redact("Janet is fine", "Jane Doe", [])[0] == "Janet is fine"           # word boundaries
    assert redact("Will will try; WILL GRANT agreed", "Grant, Will", [])[0] == "the student will try; the student agreed"


def test_exports_are_safe_and_valid():
    md = "# Plan\n\n<script>alert(1)</script>\n\n- [ ] item\n1. one\n\n```python\nprint(1)\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    html = markdown_to_html(md, "T <x>", "sub")
    assert "<script>" not in html and "&lt;script&gt;" in html and "<table>" in html and "T &lt;x&gt;" in html
    assert markdown_to_pdf_bytes(md, title="T").startswith(b"%PDF")


# ------------------------------------------------------------------ API
def test_context_triage_and_no_llm(nollm_client):
    j = nollm_client.get(BASE, headers=H).json()
    assert j["student"]["name"] == "Demo Student0005" and j["llm"]["enabled"] is False
    assert j["triage"]["needs_intervention"] is True and 2 <= len(j["recommended_modules"]) <= 3
    assert j["signals"]["missing_count"] == 3 and j["as_of"]
    # Preview works without a key; generation is refused cleanly.
    assert nollm_client.post(f"{BASE}/prompt", headers=H, json={"kind": "plan", "objectives": "x"}).status_code == 200
    r = nollm_client.post(f"{BASE}/generate", headers=H, json={"kind": "plan", "objectives": "x"})
    assert r.status_code == 503


def test_prompt_never_contains_identifiers(fake_client):
    notes = "Demo Student0005 (stu0005, SIS 90000005, stu0005@example.edu) says Student0005, Test missed lab."
    for kind in ("plan", "workbook", "objectives"):
        p = fake_client.post(f"{BASE}/prompt", headers=H, json={
            "kind": kind, "objectives": "Loops for Test", "instructor_notes": notes, "student_notes": notes}).json()
        body = p["system"] + p["user"]
        for ident in ("Student0005", "stu0005", "90000005", "@example"):
            assert ident not in body, (kind, ident)


def test_generate_streams_saves_and_exports(fake_client):
    r = fake_client.post(f"{BASE}/generate", headers=H, json={"kind": "plan", "objectives": "Loops",
                                                              "instructor_notes": "Demo Student0005 is quiet"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = sse(r.text)
    assert ev[0][0] == "meta" and ev[0][1]["redactions"] >= 1 and ev[-1][0] == "done"
    text = "".join(d for e, d in ev if e == "delta")
    assert "simulated" in text and "Student0005" not in text
    sid = ev[-1][1]["session_id"]

    # Workbook added to the same session, with instructor-chosen modules and config.
    r = fake_client.post(f"{BASE}/generate", headers=H, json={
        "kind": "workbook", "session_id": sid, "module_keys": ["exam_readiness", "bogus"],
        "workbook_config": {"tone": "Direct & Tactical", "length": "Short", "include_practice": False}})
    ev = sse(r.text)
    assert ev[0][1]["modules"][0]["key"] == "exam_readiness" and len(ev[0][1]["modules"]) == 1
    assert ev[-1][1]["session_id"] == sid

    s = fake_client.get(f"{BASE}/sessions/{sid}", headers=H).json()
    assert s["plan_md"] and s["workbook_md"] and s["workbook_config"]["tone"] == "Direct & Tactical"
    assert fake_client.get(BASE, headers=H).json()["sessions"][0]["id"] == sid

    html = fake_client.get(f"{BASE}/sessions/{sid}/export?part=workbook&fmt=html", headers=H)
    assert html.status_code == 200 and "Student Workbook | Demo Student0005" in html.text
    pdf = fake_client.get(f"{BASE}/sessions/{sid}/export?part=plan&fmt=pdf", headers=H)
    assert pdf.content.startswith(b"%PDF") and "attachment" in pdf.headers["content-disposition"]

    # Users without course access get 404; an admin (or course director) may delete others' sessions.
    assert fake_client.delete(f"{BASE}/sessions/{sid}", headers=as_user("noaccess")).status_code == 404
    assert fake_client.delete(f"{BASE}/sessions/{sid}", headers=as_user("admin")).status_code == 200
    assert fake_client.get(f"{BASE}/sessions/{sid}", headers=H).status_code == 404


def test_plan_requires_objectives_and_objectives_endpoint(fake_client):
    assert fake_client.post(f"{BASE}/generate", headers=H, json={"kind": "plan"}).status_code == 422
    r = fake_client.post(f"{BASE}/objectives", headers=H, json={"model": "gpt-4o"})
    assert r.status_code == 200 and "simulated" in r.json()["objectives"]


def test_rate_limit(fake_client):
    fake_client.app.state.llm.limiter.per_hour = 2
    for _ in range(2):
        assert fake_client.post(f"{BASE}/objectives", headers=H, json={}).status_code == 200
    r = fake_client.post(f"{BASE}/objectives", headers=H, json={})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_no_access_and_unknown_student(fake_client):
    assert fake_client.get(BASE, headers=as_user("noaccess")).status_code == 404
    assert fake_client.get(f"/api/courses/{BB_COURSE}/students/nobody/ei", headers=H).status_code == 404


def test_fake_provider_refused_in_production(settings):
    from spark_api.config import Settings
    with pytest.raises(RuntimeError):
        Settings(env="production", auth_mode="entra", secret_key="x", llm_provider="fake").validate()


def test_plan_prompt_is_30_minutes(fake_client):
    p = fake_client.post(f"{BASE}/prompt", headers=H, json={"kind": "plan", "objectives": "x"}).json()
    assert "30 minute" in p["system"] and "30 minute EI plan" in p["user"] and "60" not in p["user"].split("Generate:")[1]


def _openai_error(cls, status, code, message):
    import httpx
    r = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
                       json={"error": {"message": message, "type": code, "code": code}})
    return cls(f"Error code: {status}", response=r, body=r.json()["error"])


@pytest.mark.parametrize("name,status,code,expect", [
    ("RateLimitError", 429, "insufficient_quota", "no usable quota"),
    ("RateLimitError", 429, "rate_limit_exceeded", "rate limit for this account"),
    ("AuthenticationError", 401, "invalid_api_key", "rejected the API key"),
    ("NotFoundError", 404, "model_not_found", "isn't allowed to use the selected model"),
])
def test_openai_errors_are_explained(fake_client, monkeypatch, name, status, code, expect):
    import openai
    err = _openai_error(getattr(openai, name), status, code, "details from OpenAI")

    def boom(self, req):
        raise err
        yield  # pragma: no cover
    from spark_api.services import llm
    monkeypatch.setattr(llm.FakeProvider, "stream", boom)
    r = fake_client.post(f"{BASE}/objectives", headers=H, json={"model": "gpt-4o"})
    assert r.status_code == 502 and expect in r.json()["detail"] and "details from OpenAI" in r.json()["detail"]
    ev = sse(fake_client.post(f"{BASE}/generate", headers=H, json={"kind": "plan", "objectives": "x"}).text)
    assert ev[-1][0] == "error" and expect in ev[-1][1]["detail"]
