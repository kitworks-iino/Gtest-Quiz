"""Readiness must measure the shipped app and usable bank, not unused services."""
import pytest

from tools import release_readiness
from tools.generate_coverage_report import build_coverage_report


def test_release_readiness_static_gates_are_green():
    result = release_readiness.evaluate_release_readiness(profile="bootstrap")
    assert [check for check in result["checks"] if not check["passed"]] == []


def test_held_questions_do_not_count_toward_completion(monkeypatch):
    monkeypatch.setattr(release_readiness, "check_learning_app", lambda: {
        "passed": True, "availableCount": 497, "heldCount": 422, "chapters": [],
    })
    result = release_readiness.evaluate_release_readiness(profile="complete")
    count = next(check for check in result["checks"] if check["name"] == "readiness_profile_question_count")
    assert count["detail"]["minimum"] == 550
    assert count["detail"]["held"] > 0
    assert count["detail"]["usable_questions"] < 550
    assert not count["passed"]
    assert not result["passed"]


def test_small_usable_bank_fails_alpha_even_when_source_bank_is_large(monkeypatch):
    monkeypatch.setattr(release_readiness, "check_learning_app", lambda: {
        "passed": True, "availableCount": 31, "heldCount": 888, "chapters": [],
    })
    result = release_readiness.evaluate_release_readiness(profile="alpha")
    failed = [check["name"] for check in result["checks"] if not check["passed"]]
    assert "readiness_profile_question_count" in failed
    assert "syllabus_coverage" in failed


def test_failed_learning_behavior_blocks_readiness(monkeypatch):
    monkeypatch.setattr(release_readiness, "check_learning_app",
                        lambda: {"passed": False, "error": "session did not survive reload"})
    result = release_readiness.evaluate_release_readiness(profile="bootstrap")
    assert not result["passed"]
    assert not next(check for check in result["checks"] if check["name"] == "learning_app_smoke")["passed"]


def test_missing_node_is_a_failed_check(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("node")
    monkeypatch.setattr(release_readiness.subprocess, "run", missing)
    assert not release_readiness.check_learning_app()["passed"]


def test_readiness_profiles_keep_existing_quality_thresholds():
    report = build_coverage_report()
    assert {"bootstrap", "alpha", "beta", "complete", "expanded"} <= set(report["profiles"])
    assert report["profiles"]["complete"]["min_questions"] == 550
    assert report["profiles"]["complete"]["min_per_chapter"] == 10
    assert report["profiles"]["expanded"]["min_questions"] == 1000
    with pytest.raises(ValueError):
        release_readiness.evaluate_release_readiness(profile="pretend-complete")
