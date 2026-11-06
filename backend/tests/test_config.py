import pytest

from spark_api.config import Settings
from spark_core.ids import make_course_id, slug, split_course_id


def test_simulated_auth_refused_in_production():
    with pytest.raises(RuntimeError, match="simulated"):
        Settings(env="production", auth_mode="simulated", secret_key="x").validate()


def test_course_ids_are_strings_and_slugged():
    cid = make_course_id("bb", "202620-DEMO-CS101")
    assert split_course_id(cid) == ("bb", "202620-DEMO-CS101")
    assert slug(cid) == "bb__202620-DEMO-CS101"
    with pytest.raises(ValueError):
        split_course_id("12345")
