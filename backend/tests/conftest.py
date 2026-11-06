from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spark_api.config import Settings
from spark_api.main import create_app

BACKEND = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
BB_COURSE = "bb:202620-DEMO-CS101"


@pytest.fixture()
def bb_share(tmp_path) -> Path:
    """A fake WebDAV mount containing the synthetic demo Ultra exports."""
    root = tmp_path / "share"
    course = root / "SPARK" / "202620-DEMO-CS101"
    (course / "gradebook").mkdir(parents=True)
    (course / "grade_history").mkdir(parents=True)
    (course / "gradebook" / "gb.csv").write_bytes((FIXTURES / "demo_gradebook.csv").read_bytes())
    (course / "grade_history" / "gh.csv").write_bytes((FIXTURES / "demo_grade_history.csv").read_bytes())
    return root


@pytest.fixture()
def settings(tmp_path, bb_share) -> Settings:
    return Settings(env="test", auth_mode="simulated", data_dir=tmp_path / "var", bb_root=bb_share,
                    sim_users_file=BACKEND / "config" / "sim_users.yaml",
                    frontend_dist=tmp_path / "no-dist", secret_key="test-secret")


@pytest.fixture()
def client(settings) -> TestClient:
    return TestClient(create_app(settings))


def as_user(persona: str) -> dict:
    return {"X-Sim-User": persona}
