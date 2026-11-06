# SPARK BB Edition: Migration Plan (updated 2026-11-01)

Live, editable copy: https://claude.ai/artifact/WAcAciQZMRZ5zyem3jdNyh

## Status (2026-11-01)
Delivered as spark-bb.zip (backend/ FastAPI, frontend/ React+TS Vite with prebuilt dist/, dev_share/ stand-in mount). 121 pytest tests pass; headless browser walkthroughs verified sign-in, ingest, roster, student page, Analytics, Course Director, Data & Models, Admin (incl. admissions scrub), CSV/DB export, no-access persona, and the EI page end to end (fake provider). User runs it on Ubuntu (system Python; a source-built Python lacked _ctypes).
- Phases 0 + 1 done. Also: Blackboard file adapter with snapshot + version hash, CanvasAdapter port (untested live), FixtureAdapter, audit log, user preferences (cutoff line).
- Phase 4: Analytics, Course Director, Data & Models, EI done.
  - Analytics/Director: features (STUDENT_FEATURES) + K-means risk clusters (STUDENT_CLUSTERS) computed at ingest (SCHEMA_VERSION=2); days_since_last_submission measured to export snapshot time; director ranking fix (lower-is-better metrics ascending).
  - Data & Models (course tab): ingest history, force rebuild, audited DuckDB export; success model (spark_core/ml/success.py) with the same 3 classifiers/hyper-parameters, stratified k-fold CV accuracy + ROC AUC + majority baseline, feature importance (RF/ET), artefacts as joblib in DATA_DIR/models/<slug>/, metadata tables success_models + model_aliases; cross-course reuse (alias) limited to courses the user can access.
  - FIX: original predict_success_probability skipped progress features at prediction time (always median-imputed); now computed identically in train and predict.
  - Success probability shown on student page (with model source/AUC tooltip) and as roster column.
  - zyBooks: per-course DATA_DIR/zybooks/<slug>.duckdb (original dropped one global table each upload, erasing other courses); students matched at upload by email, email local-part vs Bb username, then first+last name; student combo chart + KPIs; class matrix on roster page. Parser follows original code's 3-row header expectations; not yet checked against a real zyBooks report (user deferred zyBooks work).
  - Admin page: course inventory; admissions upload (.xlsx/.zip), dry-run check then commit, append-only by FL_NAME_KEY, SHA-256 file log, atomic writes, compatible with Streamlit admissions.duckdb; uploaded workbooks not stored.
  - SEX and RACE removed (decision 2026-11-01): never read or stored. Legacy DBs are scrubbed on next upload or via Admin "Remove" button (POST /api/admin/admissions/scrub), which rewrites into a fresh file so old values are physically gone (verified at byte level). Missing canonical columns are added with defaults.
  - DuckDB 2.0 note: fetchone() leaves an unfinished result on the connection; use fetchall(). Dropping a column does not erase old values from the file; rewrite into a fresh file.
- EI (done 2026-11-01): page at /courses/:cid/students/:sid/ei, linked from the student page.
  - Core: backend/spark_core/ei/{signals.py, prompts.py, redact.py, export.py, methodology.md}. Module library, recommendation rules and weights, Activity Index formula (was "studious index"; renamed to avoid a clash with the Director Health Index) and trigger thresholds unchanged. Prompt text kept except: "(from Canvas)" now "(from the gradebook)", and the plan is 30 minutes (was 60-90).
  - FIXES vs original: needs_intervention crashed when a student had no grade; "missing" now = MISSED_ASSIGNMENT (not "unscored"); late and near-due rates over timed items only; days since last submission measured to the export snapshot; module student_outputs bullets ran together (missing newline).
  - Privacy: student name (full, "Last, First", lone first/last name when capitalised), username, SIS ID, STUDENT_ID and any e-mail are replaced in objectives and notes before sending; a final word-boundary leak check refuses to send (422). Prompt preview shows the exact system + user text and redaction count, and works with no API key.
  - LLM layer: spark_api/services/llm.py. LLM_PROVIDER none | openai | fake (fake refused in production), OPENAI_API_KEY server-side only, OPENAI_BASE_URL for Azure OpenAI or a gateway, LLM_MODELS, LLM_DEFAULT_MODEL, LLM_RATE_PER_HOUR (in-memory per user; move to Redis in Phase 5). Temperature capped at 0.6; retries without temperature if the model rejects it.
  - API: GET /ei (signals, triage, modules, LLM info, sessions), GET /ei/methodology, POST /ei/recommend, /ei/prompt, /ei/objectives, /ei/generate (SSE: meta, delta, done | error; saved to ei_sessions), GET/DELETE /ei/sessions/{id} (delete: author, director, admin), GET /ei/sessions/{id}/export?part=plan|workbook&fmt=html|pdf|md. Audited: ei_view, ei_objectives, ei_generate_plan, ei_generate_workbook, ei_export, ei_delete.
  - Errors (2026-11-02): OpenAI failures are explained in plain language (describe_error): insufficient_quota (no credit/budget, $0 project limit; fix in the OpenAI account), rate limit, bad key, model not allowed for the key, refused request, network/timeout, OpenAI outage; includes OpenAI's own message, never prompt content. Startup log and /api/health report which .env was loaded and why AI is disabled.
  - Exports: HTML (markdown-it with raw HTML disabled) and PDF (ReportLab port of markdown_pdf.py).
  - Frontend: routes wrapped in ErrorBoundary (shows the real error; stale lazy chunk after an update -> reload prompt). EIPage.tsx, lazy-loaded (react-markdown + remark-gfm); fetch + ReadableStream for SSE; Stop button; module override (up to 4); workbook config; saved sessions.
- Code: backend/spark_core/{ml/success.py, etl/admissions_loader.py, etl/zybooks.py, plotting_analytics.py, ei/}, backend/spark_api/{services/models.py, services/ei.py, services/llm.py, routers/data.py, routers/ei.py}, frontend/src/pages/{DataPage,AdminPage,EIPage}.tsx.
- Requirements added: scikit-learn, scipy, openpyxl, python-multipart, openai, reportlab, markdown-it-py, python-dotenv.
- Settings (2026-11-02): `.env` in the project folder is now loaded automatically (python-dotenv; real environment variables win; SPARK_ENV_FILE to point elsewhere or `none` to skip). Before this, .env.example was documentation only and copying it did nothing. Relative paths (DATA_DIR, BB_ROOT, SIM_USERS_FILE, FRONTEND_DIST) resolve against the project folder, not the folder uvicorn starts in.
- Blackboard mount operations (done 2026-11-01): deploy/webdav/ (README, davfs2.conf.spark, fstab.example, secrets.example, spark-mount-check.sh + .service/.timer, spark-poller.service).
  - Verified in a sandbox on Ubuntu 24.04 + davfs2 1.7.0 against a wsgidav WebDAV server with Basic auth: mount, read-only, spark-only permissions, new export via PUT detected and ingested, server outage, frozen mount.davfs, dropped mount + recovery. Not yet against the real Blackboard server.
  - FINDING: davfs2 1.7.0/1.7.1 on newer kernels fails every directory listing with EINVAL; `buf_size 64` in the mount section fixes it (fixed upstream in 1.7.2). Included in davfs2.conf.spark.
  - FINDING: plain `umount` on a hung davfs2 mount hangs (umount.davfs waits for the stuck process). Remount script uses `umount -l -i`, kills mount.davfs via its pid file, then mounts; every step time-limited; also checks `mountpoint -q` (an empty folder left by a dropped mount answers ls).
  - FINDING: davfs2 creates lost+found inside the mount; adapter ignores it.
  - FINDING: while Blackboard is down davfs2 serves cached files silently; SPARK probes the WebDAV URL from /proc/mounts (OPTIONS, no credentials) and reports "server not answering".
  - Adapter: fingerprint() is listing + stat only; each export copied once to DATA_DIR/snapshots (index keyed by name/size/mtime), hashed locally, rejected if it changes during copy (ExportInProgress), 5 snapshots kept per input. source_version unchanged in formula, so existing runs still match.
  - Poller (spark_api/services/poller.py): BB_POLL_INTERVAL_S (0 = off), BB_SETTLE_S (120), BB_IO_TIMEOUT_S (20), BB_REQUIRE_MOUNT. Runs in the API via lifespan; one leader across gunicorn workers via flock on DATA_DIR/poller.lock; or standalone `python -m spark_api.poller [--once]`. Table source_watch. Statuses: up_to_date, waiting, ingesting, ingested, failed (not retried until files change or admin forces), no_gradebook, busy, mount_error. All mount calls on one thread with timeout; startup discovery also time-limited. Ingest runs show triggered_by "system:poller" ("automatic" in UI).
  - API: GET /api/admin/mount, POST /api/admin/mount/poll[?course_id=] (audited mount_poll), GET /api/courses/{id}/watch. UI: Admin "Blackboard mount" panel; Data page line about automatic ingest.
- Uploaded exports (done 2026-11-01; chosen because WebDAV needs an account with a Blackboard password, and IdP/SSO sign-in can't be used for WebDAV): instructors upload Full Gradebook + Grade History on the course Data page, or add a new course from them (Courses page; uploader gets Instructor access; course ID suggested from the shared parent of Child Course IDs; optional display name). UPLOADS_ENABLED (default true); BB_ROOT may be empty (uploads only).
  - Files go to DATA_DIR/inbox/SPARK/<course>/{gradebook,grade_history}/ under SPARK-generated names (5 kept); the Bb adapter reads mount + inbox, newest file of each kind wins, so both sources can be mixed.
  - Two-step: dry run (default) identifies each file by its columns, parses them together as an ingest would, and returns errors (unrecognised file, two of a kind, first upload without a gradebook, bad course ID, course already exists without revealing who has it) and warnings (sections belong to another course, under 50% of current students match, history students don't match the gradebook, no grade history at all); commit saves then ingests immediately. Audited upload_exports.
  - API: POST /api/courses/{id}/uploads, POST /api/uploads/courses (multipart files[], dry_run, course_code, name); GET /api/courses returns can_add_courses.
  - Long-term automatic alternative if an admin will register an app once: Learn REST API with 3-legged OAuth (instructors sign in through SSO; no stored passwords). Not built.
- Removing courses (done 2026-11-01): "Remove" on each course card opens a confirmation dialog with two levels.
  - Remove from my courses (any non-admin with access): row in new table hidden_courses; the course leaves the list and course_access returns 404 until restored ("Removed courses" on the Courses page). Separate table so grant seeding can't undo it. Nothing deleted; others unaffected.
  - Delete from SPARK permanently: admin, or the only person with access (Instructor/CourseDirector). Blocked for demo courses (recreated at startup; use ENABLE_FIXTURES=false), Canvas courses (re-added by discovery) and Blackboard courses whose folder is still on the mount (checked under the mount timeout). Requires typing the course ID. Dialog lists impact: students, update history, success models, EI sessions, other courses reusing its model (their alias is dropped). Deletes rows explicitly (SQLite doesn't enforce ON DELETE CASCADE) plus course DuckDB, snapshots, models, zyBooks DB and uploaded exports; holds the per-course ingest lock. Audit log is kept. Audited hide_course, restore_course, delete_course.
  - API: GET /api/courses/{id}/removal, POST /api/courses/{id}/hide, POST /api/me/hidden-courses/{id}/restore, POST /api/courses/{id}/delete {confirm}; GET /api/courses returns hidden_courses.
- Fixes after first multi-section upload (2026-11-01; SCHEMA_VERSION 3, so the next ingest of each course rebuilds it):
  - Rows with no Child Course ID (enrolled only in the parent course: instructors, TAs, preview users) are dropped from every dataset at parse time and counted in the data notes (excluded_no_section). A course with no Child Course IDs at all becomes one section, "All students". "No Section" no longer exists for Blackboard. Upload preview counts match.
  - Current grade: BB_TOTAL_COLUMN ("Final Grade") was a custom column in the reference course; a course without it had every CURRENT_SCORE null (empty bars). Now: configured column if mostly numeric, else a calculated Percentage column named like final/overall/total/weighted, else computed from points earned / possible on graded items (matched Bb's total within 1 point on the reference course; differs with category weights). Letter-only columns are never used as scores; a Letter column is used for CURRENT_GRADE if the configured one is missing. Source shown in data notes (current_grade_source, current_grade_column). _num accepts "85.5%" and "1,234".
  - Roster sort: comparator did -Infinity - -Infinity = NaN when values were missing, giving an arbitrary order. Now blanks sort last in both directions, numbers numerically, text with numeric collation, ties by student name.
- Course list arrangement (2026-11-02): new table course_prefs (upn, course_id, position, archived, archived_at, last_opened_at), all per user.
  - Sort menu: Recently opened (default; last_opened_at recorded when a course page loads, throttled to one write a minute; never-opened courses follow by last data update), My order, Name, Last data update. Choice saved in user_preferences.prefs.course_sort (PUT /me/preferences now merges keys instead of replacing them).
  - Arrange: drag-and-drop cards (HTML5 DnD) plus Earlier/Later buttons for keyboard users; Save order -> PUT /api/me/course-order (inaccessible IDs ignored) and switches to My order. Courses added later appear first until arranged.
  - Archive: POST /api/courses/{id}/archive {archived}; per user, course still opens; collapsible Archived section with Unarchive. Distinct from Remove (which suspends access). Deleting a course also deletes its course_prefs rows.
- Local mode and themes (2026-11-03):
  - AUTH_MODE=local is now the default: no sign-in; the start page offers Instructor mode or Admin mode (spark_api/auth/local.py). The local instructor gets Instructor access to every course automatically (grant "*", re-seeded on discovery and when an admin adds a course). Header shows the mode and "Switch mode". Banner warns that anyone who can reach the server can see student data. Refused when ENV=production. AUTH_MODE=simulated (test personas, used by the test suite) and the Entra plan are unchanged.
  - Four themes, chosen from a Theme menu in the top bar and saved per browser (localStorage, spark.theme): Falcon Blue (default; Academy blue and silver), Open Sky (sky blues + Air Force golden yellow), Chapel Steel (graphite/aluminum, square corners; inspired by the Cadet Chapel's modernist architecture), Night Flight (dark). All colours are CSS custom properties (index.css :root and [data-theme]); hard-coded colours were replaced with tokens. Plotly charts follow the theme (transparent backgrounds, theme text/grid colours on every axis, legend and annotation). No official insignia or logos are used; references: Air Force blue #00308F (Pantone 287), USAFA blue and silver.
  - New start page: hero banner plus two mode cards.
Next: Phase 2/5 infra (worker, Redis, Alembic, Postgres, Compose). Course display names: optional course.json per folder. Old Performance-tab section charts (time management, category radar) not yet ported.

## Summary
Port SPARK from Streamlit + Canvas REST API to a FastAPI backend + React frontend that reads course data from a Blackboard Ultra Content Collection folder mounted over WebDAV, with a pluggable sign-in layer running on simulated Microsoft 365 (Entra ID) identities for now. Analytics core is reused; work concentrates in data extraction, identity/session, and UI. Canvas support is kept.

## Decisions (answered 2026-11-01)
| Question | Answer | Effect |
|---|---|---|
| Original or Ultra? | Ultra | Parse Ultra Full Gradebook + Grade History formats |
| REST API? | Not now, maybe later | File ingest first; adapter interface leaves room for an API adapter |
| Sections? | Merged | Child Course ID column in gradebook = section; ingest from the parent course |
| Manual exports? | Yes; upload to SPARK for now (no admin/WebDAV account available) | Download panel to My Device, then drop on the course Data page; Content Collection + WebDAV still supported |
| Due dates? | Set per assignment; prefer WebDAV over CSV | Inferred from Grade History auto-zero events (16/18 items in sample); manifest only fills gaps |
| Hosting? | Linux VM, on-prem or cloud | Docker Compose on one VM; davfs2 on host |
| Keep Canvas? | Yes | CanvasAdapter kept; namespaced course IDs (bb: / canvas:) |
| Store SEX/RACE from admissions? | No, remove | Dropped from schema and loader; scrub for legacy files |
| zyBooks validation | Later | Parser stays as ported until a real report is available |
| AI service for EI | OpenAI | LLM_PROVIDER=openai with a server-side OPENAI_API_KEY |
| Blackboard connection without an admin | Uploads now; REST API (3LO) later if an admin registers it | WebDAV and poller kept for when a service account is available |
| Sign-in for now | None: Instructor mode / Admin mode (AUTH_MODE=local) | Real O365 stays Phase 6; local mode refused in production |
| EI session length | 30 minutes | Lesson-plan prompt changed from 60-90 to 30 minutes to match the system prompt |

## Blackboard Ultra export format (notes from building the parser)
Parser: backend/adapters/bb_ultra/parser.py. Recomputed points % matches the Bb Final Grade column. The demo course in dev_share/ and the test fixtures are synthetic, generated by backend/tests/fixtures/make_demo_course.py.
- Gradebook header format: `Name [Total Pts: 20 Score] |710001` -> ASSIGNMENT_ID bb:710001. Calculated columns have "up to" or display Percentage/Letter (Final Grade, Overall Grade "Letter +/- 2", Prog Grade).
- Gradebook columns: Last Name, First Name, Username, Student ID, Last Access, Availability, Child Course ID, then items. "Needs Grading" cell values exist.
- Grade History columns: Date, Column, Grade Category, Last Edited by: Name/Username/Role, User, Username, Submission Attempt Number, Submission Receipt Number, Type (Grade/Override), Value, Attempt Activity, Event, Formative Indicator. Date format "Jan 08, 2027 11:42:10 PM".
- Roles: S student, P instructor, empty = system (automatic zeros, value 0, just after midnight -> due date 23:59 prior day).
- Submission time = Attempt Activity on role S events; only online homework has it. Quizzes/Graded Reviews are instructor overrides with no submit time -> lateness over timed items only.
- Joins: Username in both files; history Column name = gradebook item name (20/20 matched). 1 history user not in gradebook -> reported as a count, not ingested.
- Items with no scores yet treated as inactive; Needs Grading excluded rather than zero.

## Key architecture decisions
- Backend: FastAPI, gunicorn+uvicorn workers. Frontend: React + TypeScript (Vite), TanStack Query, plotly.js-dist-min lazy-loaded (backend returns Plotly fig JSON; FigureCard fetches any figure URL).
- Jobs: currently a per-course-locked thread in the API (ingest, training run in threadpool); move to RQ or arq on Redis.
- Metadata DB: SQLAlchemy (SQLite dev, Postgres prod): courses, course_access, ingest_runs, audit_events, user_preferences, success_models, model_aliases, ei_sessions, source_watch, hidden_courses, course_prefs. Alembic before first prod deploy.
- Analytics store: per-course DuckDB in DATA_DIR/courses/<slug>.duckdb; API reads read-only; writer builds temp file + os.replace. Same atomic pattern for admissions.duckdb and zybooks/<slug>.duckdb.
- Blackboard access: read-only mount at BB_ROOT; newest file per folder snapshotted to DATA_DIR/snapshots by hash; DuckDB never on the mount.
- Course IDs: namespaced strings (bb:, canvas:, fixture:); slug() for file names.
- Auth: SimulatedEntraProvider from sim_users.yaml (instructor, director, admin, noaccess); signed HttpOnly cookie (itsdangerous); X-Sim-User header in non-prod for tests; refuses ENV=production. No-access returns 404. Admin-only: admissions upload/scrub, inventory, discover. Instructor+: ingest, train, alias, zyBooks upload, DB export, EI.
- Audited actions: view_roster, view_student, view_analytics, export_risk_report, view_director, view_section_roster, export_course_db, train_success_model, set_model_alias, upload_zybooks, upload_admissions, scrub_admissions, upload_exports, mount_poll, hide_course, restore_course, delete_course, ei_view, ei_objectives, ei_generate_plan, ei_generate_workbook, ei_export, ei_delete.
- Uploads: UPLOAD_MAX_MB per file (default 200, all upload types); Blackboard exports are streamed to DATA_DIR/tmp in 1 MB chunks (never held whole in memory) and the scratch folder is always removed; reverse proxy needs a matching body-size limit (nginx client_max_body_size). Extension checks. Admissions/zyBooks files are not retained; Blackboard exports are kept in DATA_DIR/inbox (last 5 per kind) because they are the course's data source.
- LLM: server-side key, SSE streaming, per-user rate limit, identifier redaction + leak check (done).

## Blackboard data flow
1. Parent course > Gradebook > Download > Full Gradebook, CSV, save to Content Collection /SPARK/<course>/gradebook/
2. Same panel > Grade History > /grade_history/
3. Ingest snapshots + hashes, rebuilds DuckDB (skips if unchanged and same schema); UI shows data age and data notes.
Folder contract: /SPARK/<bb_parent_course_id>/{gradebook, grade_history, manifest/assignments.csv}/

## Phases
0. Foundations: DONE
1. Platform skeleton: DONE
2. Data layer: adapters + ingest + runs DONE; worker queue pending
3. Blackboard adapter: parser + snapshot + validation notes + mount ops (davfs2 config, poller, self-healing) DONE; real-server test pending
4. Feature parity: Analytics, Course Director, Data & Models, EI DONE
5. Multi-user hardening (1-2 wk): worker, Redis cache keyed by ingest run, shared rate limits, Locust (100 users, p95 < 1 s), Compose deploy, backups
6. Real O365: Entra app registration, EntraIdProvider, role mapping

## Still to confirm
1. Who runs exports and how often
2. (Only for the WebDAV option) Service account with a Blackboard password (WebDAV uses Basic auth; SSO-only accounts can't mount) and read access to the SPARK folder; the folder's WebDAV URL
3. A zyBooks Class Roster Report (de-identified, kept out of the repository) to validate the parser (deferred)
4. (Only for the WebDAV option) Whether instructors can save exports into a shared SPARK folder (write access to their own course subfolder), or exports should live in each course's own Content Collection folder (would need a second folder layout)
