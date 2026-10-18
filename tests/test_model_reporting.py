"""Model upgrades preserve valid history and keep generation evidence explicit."""

import json

import pytest

from gtest_quiz.bank_epoch import current_bank_version
from tools.generate_coverage_report import build_coverage_report


def report_fixture(tmp_path, rows, **metadata):
    bank_path = tmp_path / "bank.jsonl"
    bank_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    meta_path = tmp_path / "meta.json"
    meta = {
        "chapters": {"group": {"label": "学習", "subchapters": {"chapter": {"label": "機械学習"}}}},
        **metadata,
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return build_coverage_report(
        bank_path=bank_path,
        meta_path=meta_path,
        review_queue_path=tmp_path / "no-review-queue.jsonl",
    )


def question(model="gemini-3.5-flash", **overrides):
    return {
        "question": "教師あり学習で利用するデータは何ですか？",
        "chapter_id": "機械学習",
        "difficulty": "basic",
        "correct_index": 0,
        "bank_version": current_bank_version(),
        "provenance": {"model": model, "accepted_with_review_warnings": False},
        **overrides,
    }


def test_auto_upgrades_do_not_invalidate_historical_models(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "auto")
    selection = {
        "model_id": "gemini-3.8-flash",
        "eligible_models": ["gemini-3.8-flash", "gemini-3.5-flash"],
        "checked_at": "2026-10-18T00:00:00Z",
        "pricing_url": "https://ai.google.dev/gemini-api/docs/pricing",
        "reason": "newest free-tier general-text model",
    }
    rows = [question(), question("gemini-3.8-flash", question="誤差逆伝播法の目的は何ですか？")]
    report = report_fixture(
        tmp_path, rows,
        last_refill_result={"generated": 1, "model": "gemini-3.8-flash", "model_selection": selection},
    )
    assert report["profiles"]["bootstrap"]["passed"]
    assert report["model_selection_policy"] == "auto"
    assert report["model"] == report["last_selected_model"] == "gemini-3.8-flash"
    assert report["model_distribution"] == {"gemini-3.5-flash": 1, "gemini-3.8-flash": 1}
    assert report["model_mismatch_count"] == report["model_provenance_missing_count"] == 0
    assert report["last_model_selection"] == selection


@pytest.mark.parametrize("provenance", [None, [], {}, {"model": None}, {"model": 42}, {"model": " "}, {"model": "AUTO"}])
def test_unresolved_or_missing_provenance_blocks_quality_gates(tmp_path, monkeypatch, provenance):
    monkeypatch.setenv("GEMINI_MODEL", "auto")
    report = report_fixture(tmp_path, [question(provenance=provenance)])
    assert report["model_provenance_missing_count"] == 1
    assert report["model_distribution"] == {}
    assert not report["profiles"]["bootstrap"]["passed"]


def test_fixed_model_mismatch_is_diagnostic_for_historical_questions(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    report = report_fixture(tmp_path, [question()])
    assert report["model_mismatch_count"] == 1
    assert report["profiles"]["bootstrap"]["passed"]


def test_selection_without_generation_does_not_relabel_last_generated_model(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "auto")
    report = report_fixture(
        tmp_path, [question()],
        last_refill_result={
            "generated": 0,
            "model": "gemini-3.8-flash",
            "model_selection": {"model_id": "gemini-3.8-flash"},
        },
        content_factory={"generated": 10, "model": "gemini-3.5-flash"},
    )
    assert report["last_selected_model"] == "gemini-3.8-flash"
    assert report["model"] == "gemini-3.5-flash"


def test_no_generation_history_does_not_report_auto_as_generated_model(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "auto")
    report = report_fixture(
        tmp_path, [question()],
        last_refill_result={"generated": 0, "model": "auto"},
    )
    assert report["model"] is None
    assert report["last_selected_model"] is None
    assert report["last_model_selection"] is None
