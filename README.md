# SPARK BB Edition

SPARK (Student Performance Analysis & Reporting Kit) rebuilt as a web app:
**FastAPI + React**, reading **Blackboard Ultra** exports from a WebDAV-mounted
Content Collection folder, with **Canvas** still supported. Sign-in is a
**simulated Microsoft 365 (Entra ID)** flow until real O365 is connected.

Done so far: Phases 0 and 1 (platform skeleton, data pipeline, roster and student
views) and Phase 4: Analytics, Course Director, Data & Models, and Extra
Instruction (EI) lesson plans and intervention workbooks.

## What works now

| Area | Status |
|---|---|
| Blackboard Ultra parser | Full Gradebook + Grade History; sections from *Child Course ID*; due dates inferred from automatic zeros; checked against real exports (none are in this repository) |
| Ingest | Mount -> local snapshot (hashed) -> parse -> per-course DuckDB, atomic swap; unchanged inputs are skipped |
| Uploading exports | Instructors drop the Full Gradebook and Grade History files on the course's Data page, or add a new course from them. Files are recognised by their columns, checked before saving (wrong course, mismatched files, unparseable exports), then ingested. No WebDAV or Blackboard admin needed |
| Course list | Most recently opened first by default; sort by name or last update; Arrange (drag and drop) for your own order; Archive courses you're done with (they still open, from the Archived section) |
| Removing courses | "Remove" on a course card: remove it from your own list (restorable), or, if you're the only person with access or an admin, delete it and all its data permanently after typing the course ID |
| Mount watching | Poller ingests new exports automatically (stat-only checks, settle time, one copy per export); hung or dropped WebDAV mounts time out instead of blocking; Admin page shows mount and per-course status. Setup: `deploy/webdav/README.md` |
| Auth | Simulated Entra ID personas, signed HttpOnly session cookie, refuses to run in production |
| Access control | Per-course grants; no-access returns 404 (course IDs can't be probed); Admin sees all |
| Audit | Every roster and student view, EI generation and export is logged (who, what, when) |
| Extra Instruction | Triage with reasons, Activity Index, recommended modules, AI lesson plan and student workbook (streamed), prompt preview, saved sessions, HTML/PDF export. Student name, username, ID and e-mail are removed from prompts; the API key stays on the server |
| UI | Sign in, course list with ingest + data notes, section roster, student page with KPIs, risk badge and two charts |
| Analytics | Risk clusters (K-means k=4, same six features and ordering as before) computed at ingest; 3D PCA with hulls and 2D PCA, highlight a student, six cluster-insight charts, admissions context (when an admissions DB is present), audited CSV risk report |
| Data & Models | Per course: ingest history, force rebuild, audited DuckDB export; success model training (same 3 classifiers) with cross-validated accuracy/AUC and feature importance; reuse another course's model; per-course zyBooks upload matched to the roster |
| Admin | Course inventory; admissions upload (.xlsx or .zip, dry-run check first, append-only, compatible with the Streamlit admissions.duckdb) |
| Success probability | On the student page and as a roster column, with which model produced it |
| Course Director | Section KPIs, rank by any metric (lower-is-better metrics now rank correctly), ranked bar, grade distribution by section, Health Index explainer, per-section roster least-healthy first |
| Canvas | Adapter ported from the original client (not yet run against a live Canvas here) |
| Demo data | Two synthetic courses, no real students |

## Run it on your own computer (Docker only)

See [deploy/local/README.md](deploy/local/README.md): copy `docker-compose.yml` and `.env`
into a folder, `docker compose up -d`, open
http://localhost:8000. The container updates itself from this repository on every start.

## Quick start (development)

Requirements: Python 3.11+, Node 20+.

```bash
# 0. Settings: copy the example once, then edit .env (it stays next to README.md)
cp .env.example .env                                   # Windows: copy .env.example .env

# 1. Backend
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q
uvicorn spark_api.main:app --reload --port 8000

# 2. Frontend (second terminal)
cd frontend
npm install
npm run dev                                            # http://localhost:5173
```

Open http://localhost:5173 and choose **Instructor mode** or **Admin mode** (no password; see
`AUTH_MODE` in `.env.example`). Click **Ingest exports** on the Blackboard course, then **Open**.
The **Theme** button in the top bar switches between four colour themes (saved per browser).

Single-process alternative: `cd frontend && npm run build`, then run only uvicorn and
open http://localhost:8000 (the API serves the built app).

## Blackboard data flow

```
Blackboard Ultra  --(Gradebook > Download, save to Content Collection)-->  /SPARK/<course>/gradebook/
                  --(Gradebook > Download > Grade History, same)------->  /SPARK/<course>/grade_history/
       WebDAV mount (read-only) at BB_ROOT
            |  SPARK copies the newest file in each folder to DATA_DIR/snapshots (by hash)
            v
       parse (adapters/bb_ultra/parser.py) -> transform_data() -> DATA_DIR/courses/<course>.duckdb
```

Folder contract under `BB_ROOT`:

```
SPARK/
  <parent course id>/          e.g. 202620-DEMO-CS101 (becomes course id bb:202620-DEMO-CS101)
    gradebook/                 Full Gradebook exports (.csv or Ultra .xls); newest wins
    grade_history/             Grade History exports; newest wins
    manifest/assignments.csv   optional: ASSIGNMENT_NAME,DUE_AT  (fills or overrides inferred due dates)
```

`dev_share/` is a stand-in for the mount and holds a **synthetic** demo course (made-up students, generated by
`backend/tests/fixtures/make_demo_course.py`; no real student data is in this repository).

Notes the UI surfaces after each ingest:
* Due dates are inferred from Ultra's automatic zeros (marked *inferred*); items with no
  non-submitters (or not yet closed) have no due date until a manifest supplies one.
* Only online submissions carry a submission time; quizzes/exams entered by override do
  not, so lateness is computed over timed items only.
* "Needs Grading" cells are left out rather than counted as zero; future items with no
  scores are skipped.

## Layout

```
backend/
  spark_core/          LMS-neutral analytics (ported from the Streamlit app, no Streamlit imports)
    etl/transform.py   transform_data(), unchanged logic
    etl/store.py       per-course DuckDB: write via temp file + atomic swap, read-only readers
    ml/features.py     build_features() (lateness over timed items only)
    plotting.py        figures return go.Figure; settings are arguments
    ids.py             namespaced string course IDs (bb:, canvas:, fixture:)
  adapters/            one per LMS, all producing the same CourseData frames
    bb_ultra/          parser.py + adapter.py (mount, snapshot, version hash)
    canvas/            original Canvas client + extractors
    fixture/           synthetic demo courses
  spark_api/           FastAPI app
    auth/              User model, signed session cookie, simulated Entra provider
    routers/           /auth/*, /api/courses/*, /api/me/*
    services/          ingest + read queries
    db.py              metadata DB (courses, access grants, ingest runs, audit, preferences)
  config/sim_users.yaml  simulated O365 personas and their course access
  tests/               parser, API, auth/access, config guard
frontend/              React + TypeScript (Vite), TanStack Query, plotly.js
dev_share/             stand-in for the WebDAV mount (synthetic demo course)
```

## Enabling AI generation for EI

EI works without an AI service: triage, modules and the prompt preview (copy and paste
it into an approved tool). To generate in SPARK, set these in `.env` (in the project
folder, next to this README) and restart the backend:

```
LLM_PROVIDER=openai
OPENAI_API_KEY=...
# OPENAI_BASE_URL=...      # optional: Azure OpenAI or an institutional gateway
LLM_RATE_PER_HOUR=30
```

SPARK loads `.env` automatically; a variable exported in the shell or set by the service
manager takes precedence over the file. `.env` is in `.gitignore`, so the key is never
committed. `LLM_PROVIDER=fake` gives canned responses for demos (refused when `ENV=production`).
Check your institution's data agreement before pointing SPARK at any AI service: grades and
notes (without identifiers) are sent.

## Swapping in real Microsoft 365 (Phase 6)

1. Register an app in Entra ID with app roles `Instructor`, `CourseDirector`, `Admin`.
2. Add `spark_api/auth/entra.py`: OIDC authorization code + PKCE (MSAL), callback builds a
   `User` from the ID token claims and issues the same session cookie.
3. Set `AUTH_MODE=entra`. Routes, course grants and the UI do not change.

## Not yet done (by plan phase)

* **Phase 2/5:** ingest runs in a thread inside the API process (serialised per course).
  Move it to an RQ/arq worker on Redis; add Redis caching keyed by ingest run;
  Alembic migrations; Postgres; Docker Compose; load test.
* zyBooks parsing follows the original code's expectations; verify against a real
  Class Roster Report. Existing Streamlit `admissions.duckdb` files can be copied into `DATA_DIR`.
* Section-level time-management and category-radar charts from the old Performance tab.
* Course display names (currently the folder name); an optional `course.json` in each
  course folder is the simple fix.
