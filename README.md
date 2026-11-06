# SPARK

**SPARK** (Student Performance Analysis & Reporting Kit) helps instructors see how each
student is doing from their **Blackboard Ultra** gradebook: risk levels, missing and late
work, section comparisons, success predictions, and AI-assisted extra-instruction plans.
It runs on your own computer; your course data never leaves it (unless you turn on AI
generation, see Settings).

## Install

### Requirements

| | |
|---|---|
| **Docker** | [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows, macOS, Linux), or an equivalent: [OrbStack](https://orbstack.dev) or [Rancher Desktop](https://rancherdesktop.io) on macOS, [Docker Engine](https://docs.docker.com/engine/install/) with the Compose plugin on Linux. Install it and start it once. |
| **System** | Windows 10/11, macOS 12+, or a recent Linux. 4 GB free memory and about 3 GB of disk space. |
| **Internet** | For the install and for updates. SPARK keeps working offline once installed. |

Nothing else is needed: no Python, Node or git.

### macOS and Linux

Open **Terminal** and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/TheCodeheadMT/SPARK_DOCKER/main/install.sh | sh
```

### Windows

Open **PowerShell** (Start menu → type *PowerShell*) and paste:

```powershell
irm https://raw.githubusercontent.com/TheCodeheadMT/SPARK_DOCKER/main/install.ps1 | iex
```

### What happens

The installer checks Docker (and starts Docker Desktop if it isn't running), puts SPARK in
a `spark` folder in your home folder, starts it, and opens **http://localhost:8000** in
your browser. The first start takes about 3-5 minutes while it downloads and builds; later
starts take seconds. There are no questions to answer.

On the start page choose **Instructor mode** or **Admin mode** (there is no password: SPARK
is only reachable from your own computer). Add a course by uploading the **Full Gradebook**
and **Grade History** exports from Blackboard Ultra.

## Using SPARK

SPARK starts by itself whenever Docker starts. The installer also adds a `spark` command
(on Windows, open a new terminal window first):

| Command | What it does |
|---|---|
| `spark open` | Open SPARK in the browser |
| `spark stop` / `spark start` | Stop or start SPARK |
| `spark restart` | Restart and **update to the latest version** |
| `spark update` | Re-run the installer (also updates the `spark` command) |
| `spark logs` | Show what SPARK is doing (Ctrl+C to stop) |
| `spark status` | Is it running, and on which address |
| `spark uninstall` | Remove SPARK (your data folder is kept) |

If `spark` is not found on macOS/Linux, use `~/spark/spark` instead (for example
`~/spark/spark open`).

**Updates:** SPARK checks for a new version every time it starts, so `spark restart` (or
restarting your computer) is all it takes. Without internet it starts the version it has.

**Your data** is in the `spark-data` folder inside the install folder (`~/spark/spark-data`,
or `%USERPROFILE%\spark\spark-data` on Windows). Back up that folder; nothing else needs
keeping.

## Settings

Settings are in `.env` in the install folder (`~/spark/.env`, or `%USERPROFILE%\spark\.env`).
Edit it, then run `spark start`. The common ones:

| Setting | Default | |
|---|---|---|
| `SPARK_PORT` | `8000` | The address is http://localhost:SPARK_PORT. The installer picks the next free port if 8000 is taken |
| `SPARK_REF` | `main` | Version to run. Set a release tag (e.g. `v0.1.0-rc1`) to stay on that version |
| `ENABLE_FIXTURES` | `false` | `true` adds two demo courses with made-up students, to look around |
| `LLM_PROVIDER`, `OPENAI_API_KEY` | `none` | AI-generated lesson plans and workbooks in Extra Instruction. Grades and notes (never names or IDs) are sent to the AI service, so check your institution's rules first |

Installer options, for unattended or custom installs: set `SPARK_HOME` (install folder),
`SPARK_PORT`, `SPARK_REF` or `SPARK_NO_OPEN=1` before the command, for example

```bash
curl -fsSL https://raw.githubusercontent.com/TheCodeheadMT/SPARK_DOCKER/main/install.sh | SPARK_PORT=8080 sh
```

```powershell
$env:SPARK_PORT = "8080"; irm https://raw.githubusercontent.com/TheCodeheadMT/SPARK_DOCKER/main/install.ps1 | iex
```

## Troubleshooting

| Problem | Fix |
|---|---|
| *Docker is not installed* / *not running* | Install Docker Desktop (see Requirements), start it, wait until it says it is running, then run the install command again |
| Linux: *your user can't use Docker* | `sudo usermod -aG docker $USER`, log out and back in, run the install command again |
| *Port 8000 is in use* | Run the installer with another port (see Installer options), or change `SPARK_PORT` in `.env` and run `spark start` |
| The page doesn't load after a restart | It may still be updating: `spark logs` shows progress. Wait for *starting SPARK* |
| Something broke after an update | Set `SPARK_REF` in `.env` to the last version that worked (a tag or commit), then `spark restart`. Your data is not affected |

Running the install command again is always safe: it updates SPARK and keeps your data
and settings. How the container updates itself is described in
[deploy/local/README.md](deploy/local/README.md).

---

## For developers

The rest of this page is about working on SPARK itself.

SPARK BB Edition is the original SPARK rebuilt as a web app: **FastAPI + React**, reading
Blackboard Ultra exports (uploaded, or from a WebDAV-mounted Content Collection folder),
with **Canvas** still supported. The demo course in `dev_share/` and all test data are
synthetic (generated by `backend/tests/fixtures/make_demo_course.py`).

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
