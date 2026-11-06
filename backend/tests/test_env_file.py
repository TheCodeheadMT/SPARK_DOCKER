import os

from spark_api import config


def test_env_file_is_loaded_and_relative_paths_resolve(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=fake           # comment after the value\n"
                   "BB_TOTAL_COLUMN=Final Grade     # value with a space\n"
                   "DATA_DIR=./var\n"
                   "BB_ROOT=\n"
                   "# OPENAI_API_KEY=sk-commented-out\n"
                   "LLM_RATE_PER_HOUR=7\n")
    for k in ("LLM_PROVIDER", "BB_TOTAL_COLUMN", "DATA_DIR", "BB_ROOT", "OPENAI_API_KEY", "LLM_RATE_PER_HOUR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SPARK_ENV_FILE", str(env))
    monkeypatch.setenv("LLM_RATE_PER_HOUR", "9")            # a real variable beats the file
    try:
        s = config.load_settings()
        assert s.llm_provider == "fake" and s.bb_total_column == "Final Grade" and s.llm_rate_per_hour == 9
        assert s.data_dir == config.REPO_DIR / "var" and s.bb_root is None
        assert s.openai_api_key == ""
    finally:
        for k in ("LLM_PROVIDER", "BB_TOTAL_COLUMN", "DATA_DIR", "BB_ROOT"):
            os.environ.pop(k, None)


def test_env_file_can_be_skipped(monkeypatch):
    monkeypatch.setenv("SPARK_ENV_FILE", "none")
    assert config.load_env_file() is None


def test_disabled_reason_explains_missing_key(settings):
    from fastapi.testclient import TestClient
    from spark_api.main import create_app
    settings.llm_provider, settings.openai_api_key = "openai", ""
    h = TestClient(create_app(settings, start_poller=False)).get("/api/health").json()
    assert h["ai"]["enabled"] is False and "OPENAI_API_KEY is empty" in h["ai"]["disabled_reason"]
    assert "sk-" not in str(h)
