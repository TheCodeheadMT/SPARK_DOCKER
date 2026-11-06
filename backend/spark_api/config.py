"""Runtime settings, read from environment variables (see .env.example).

A `.env` file in the project folder (next to README.md) is loaded automatically, so
settings can live there instead of being exported in the shell. Real environment
variables win over the file. Relative paths in settings (DATA_DIR=./var) are taken
relative to the project folder, wherever the server is started from.
Set SPARK_ENV_FILE to use a different file, or SPARK_ENV_FILE=none to skip it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


def _bool(v: str | None, default: bool = False) -> bool:
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    env: str = "development"                 # development | test | production
    auth_mode: str = "local"                 # local (no sign-in) | simulated (test personas) | entra (Phase 6)
    secret_key: str = "dev-only-change-me"
    session_max_age_s: int = 8 * 3600
    data_dir: Path = REPO_DIR / "var"
    database_url: str = ""                   # default: sqlite file in data_dir
    sim_users_file: Path = BACKEND_DIR / "config" / "sim_users.yaml"
    bb_root: Path | None = REPO_DIR / "dev_share"
    enable_fixtures: bool = True
    canvas_base_url: str = ""
    canvas_api_token: str = ""
    bb_total_column: str = "Final Grade"
    bb_letter_column: str = "Overall Grade"
    late_grace_minutes: int = 0
    # Blackboard mount watching
    bb_poll_interval_s: int = 0              # 0 = off; 300 suggested on the server
    bb_settle_s: int = 120                   # an export must be unchanged this long before it is ingested
    bb_io_timeout_s: int = 20                # give up on a mount call after this long (hung WebDAV)
    uploads_enabled: bool = True             # instructors can upload Blackboard exports to SPARK
    upload_max_mb: int = 200                 # per file, for every upload in SPARK
    bb_require_mount: bool = False           # true on the server: an unmounted BB_ROOT/SPARK is an error
    frontend_dist: Path = REPO_DIR / "frontend" / "dist"
    env_file: str | None = None              # which .env was loaded (shown on /api/health), None if none
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5173"])
    # EI / LLM
    llm_provider: str = "none"               # none | openai | fake
    openai_api_key: str = ""
    openai_base_url: str = ""                # blank = api.openai.com; set for Azure OpenAI or a gateway
    llm_models: list[str] = field(default_factory=lambda: ["gpt-5.2", "gpt-5.1", "gpt-4o", "gpt-4o-mini"])
    llm_default_model: str = "gpt-4o-mini"
    llm_rate_per_hour: int = 30

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'spark_meta.db'}"

    def validate(self) -> None:
        if self.is_production and self.auth_mode in ("simulated", "local"):
            raise RuntimeError(f"AUTH_MODE={self.auth_mode} has no sign-in and is not allowed when ENV=production.")
        if self.is_production and self.secret_key == "dev-only-change-me":
            raise RuntimeError("Set SECRET_KEY in production.")
        if self.llm_provider not in {"none", "openai", "fake"}:
            raise RuntimeError(f"Unknown LLM_PROVIDER {self.llm_provider!r}")
        if self.is_production and self.llm_provider == "fake":
            raise RuntimeError("LLM_PROVIDER=fake is not allowed when ENV=production.")
        if self.auth_mode not in {"local", "simulated", "entra"}:
            raise RuntimeError(f"Unknown AUTH_MODE {self.auth_mode!r}")


def load_env_file() -> Path | None:
    """Copy KEY=value pairs from .env into os.environ without overriding real variables."""
    choice = os.environ.get("SPARK_ENV_FILE", "")
    if choice.lower() == "none":
        return None
    path = Path(choice) if choice else REPO_DIR / ".env"
    if not path.is_file():
        return None
    from dotenv import dotenv_values
    for key, value in dotenv_values(path).items():
        if value is not None and key not in os.environ:
            os.environ[key] = value
    return path


def _path(value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (REPO_DIR / p).resolve()


def load_settings() -> Settings:
    env_path = load_env_file()
    e = os.environ.get
    s = Settings()
    s.env_file = str(env_path) if env_path else None
    s.env = e("ENV", s.env)
    s.auth_mode = e("AUTH_MODE", s.auth_mode)
    s.secret_key = e("SECRET_KEY", s.secret_key)
    s.data_dir = _path(e("DATA_DIR", str(s.data_dir)))
    s.database_url = e("DATABASE_URL", s.database_url)
    s.sim_users_file = _path(e("SIM_USERS_FILE", str(s.sim_users_file)))
    bb = e("BB_ROOT")
    if bb is not None:
        s.bb_root = _path(bb) if bb else None
    s.enable_fixtures = _bool(e("ENABLE_FIXTURES"), s.enable_fixtures)
    s.canvas_base_url = e("CANVAS_BASE_URL", s.canvas_base_url)
    s.canvas_api_token = e("CANVAS_API_TOKEN", s.canvas_api_token)
    s.bb_total_column = e("BB_TOTAL_COLUMN", s.bb_total_column)
    s.bb_letter_column = e("BB_LETTER_COLUMN", s.bb_letter_column)
    s.late_grace_minutes = int(e("LATE_GRACE_MINUTES", str(s.late_grace_minutes)))
    s.bb_poll_interval_s = int(e("BB_POLL_INTERVAL_S", str(s.bb_poll_interval_s)))
    s.bb_settle_s = int(e("BB_SETTLE_S", str(s.bb_settle_s)))
    s.bb_io_timeout_s = int(e("BB_IO_TIMEOUT_S", str(s.bb_io_timeout_s)))
    s.uploads_enabled = _bool(e("UPLOADS_ENABLED"), s.uploads_enabled)
    s.upload_max_mb = int(e("UPLOAD_MAX_MB", str(s.upload_max_mb)))
    s.bb_require_mount = _bool(e("BB_REQUIRE_MOUNT"), s.bb_require_mount)
    s.frontend_dist = _path(e("FRONTEND_DIST", str(s.frontend_dist)))
    if e("CORS_ORIGINS"):
        s.cors_origins = [o.strip() for o in e("CORS_ORIGINS").split(",") if o.strip()]
    s.llm_provider = e("LLM_PROVIDER", s.llm_provider)
    s.openai_api_key = e("OPENAI_API_KEY", s.openai_api_key)
    s.openai_base_url = e("OPENAI_BASE_URL", s.openai_base_url)
    if e("LLM_MODELS"):
        s.llm_models = [m.strip() for m in e("LLM_MODELS").split(",") if m.strip()]
    s.llm_default_model = e("LLM_DEFAULT_MODEL", s.llm_default_model)
    s.llm_rate_per_hour = int(e("LLM_RATE_PER_HOUR", str(s.llm_rate_per_hour)))
    s.validate()
    return s


@lru_cache
def get_settings() -> Settings:
    return load_settings()
