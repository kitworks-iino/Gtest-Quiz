from pathlib import Path

from tools import release_readiness
from tools.release_readiness import evaluate_release_readiness
from tools.generate_coverage_report import build_coverage_report


def test_release_readiness_static_gates_are_green(monkeypatch):
    monkeypatch.setattr(release_readiness, "write_coverage_report", build_coverage_report)
    result = evaluate_release_readiness(profile="bootstrap")
    failed = [check["name"] for check in result["checks"] if not check["passed"]]
    assert failed == []


def test_alpha_readiness_is_not_reached_by_bootstrap_bank(tmp_path, monkeypatch):
    # This gate must stay meaningful as the checked-in bank grows beyond alpha.
    small_bank = tmp_path / "bootstrap.jsonl"
    lines = Path("bank/question_bank.jsonl").read_text(encoding="utf-8").splitlines()[:31]
    small_bank.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = build_coverage_report(bank_path=small_bank)
    questions = release_readiness.get_all_questions()[:31]
    monkeypatch.setattr(release_readiness, "get_all_questions", lambda: questions)
    monkeypatch.setattr(release_readiness, "write_coverage_report", lambda: report)
    result = evaluate_release_readiness(profile="alpha")
    assert not result["passed"]
    failed = [check["name"] for check in result["checks"] if not check["passed"]]
    assert "readiness_profile_question_count" in failed
    assert "coverage_report_quality" in failed


def test_coverage_report_profiles_are_explicit():
    report = build_coverage_report()
    assert report["bank_version"] == "gemini35_v1"
    assert {"bootstrap", "alpha", "beta", "complete", "expanded"} <= set(report["profiles"])
    assert report["profiles"]["complete"]["min_questions"] == 550
    assert report["profiles"]["expanded"]["min_questions"] == 1000
