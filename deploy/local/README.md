# SPARK on your own computer (local release)

The only thing you need is **Docker Desktop** (or OrbStack, Rancher Desktop, Podman
Desktop with Compose). No Python, Node or git.

SPARK runs in **local mode**: no sign-in, you choose *Instructor mode* or *Admin mode* on
the start page. It is only reachable from this computer.

## First-time setup

1. Make a folder, e.g. `SPARK`, and download `docker-compose.yml` into it (or save it from
   this folder on GitHub):

   ```bash
   curl -fLO https://raw.githubusercontent.com/TheCodeheadMT/SPARK_DOCKER/main/deploy/local/docker-compose.yml
   ```

   Optionally save `.env.example` next to it as `.env` to change settings.
2. Open a terminal in that folder and run:

   ```bash
   docker compose up -d
   ```

   The first start downloads and builds everything (about 3-5 minutes). Follow it with
   `docker compose logs -f` until you see `starting SPARK`, then open
   **http://localhost:8000**.

SPARK starts empty: add a course by uploading your Blackboard Ultra Full Gradebook and
Grade History exports. To look around first, set `ENABLE_FIXTURES=true` in `.env` for two
demo courses with made-up students.

## Day to day

| To | Run (in the SPARK folder) |
|---|---|
| Update to the latest version | `docker compose restart` |
| Stop | `docker compose down` |
| Start again | `docker compose up -d` |
| See what it's doing | `docker compose logs -f` |

SPARK checks for a new version **every time the container starts**, including when
Docker Desktop starts. Only what changed is rebuilt, so an update usually takes seconds
(a minute or two if the web app changed). Without an internet connection it starts the
version it downloaded last.

To stay on one version instead of following `main`, set `SPARK_REF` in `.env` to a release
tag (e.g. `v0.1.0-rc1`) or a commit, then `docker compose restart`.

## Your data

Courses, uploads and settings are in the `spark-data` folder next to `docker-compose.yml`.
Back that folder up; nothing else needs keeping. Deleting it starts SPARK empty.

The downloaded code and build live in a Docker volume (`spark_spark-code`). It is safe to
delete (`docker compose down -v`); the next start downloads it again.

## Settings

Edit `.env` (copied from `.env.example`), then `docker compose up -d` to apply. See `.env.example` for the options:
port, demo courses, AI generation for Extra Instruction (`LLM_PROVIDER=openai` and
`OPENAI_API_KEY`), and gradebook column names.

## Troubleshooting

* **`could not download SPARK`** on the first start: check the internet connection and
  `SPARK_REF` (it must be a branch, tag or commit that exists).
* **Port 8000 already in use**: set `SPARK_PORT=8001` in `.env` and open
  http://localhost:8001.
* **Something broke after an update**: set `SPARK_REF` to the last version that worked and
  `docker compose restart`. Your data is not affected.

## How it works (for maintainers)

The image built from `docker-compose.yml` contains only Python 3.11, Node 22, git and a
small bootstrap script. On every start the bootstrap fetches `SPARK_REF` into the code
volume and runs [`start.sh`](start.sh) from the fetched code, so the start logic updates
with the app. `start.sh` reinstalls Python packages only when `backend/requirements.txt`
changed, rebuilds the frontend only when `frontend/` changed (keeping the previous build if
a build fails), creates a `SECRET_KEY` once in `spark-data/.secret_key`, and starts uvicorn,
which serves the API and the built app on one port.

Changes to the bootstrap itself (in `docker-compose.yml`) reach users only when they get a
new copy of that file and run `docker compose up -d --build`, so keep it small.
