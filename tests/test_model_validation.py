"""Automatic selection must preserve real generation history in the bank gate."""

import json

import pytest

from gtest_quiz.bank_epoch import current_bank_version
from tools.review_generated_queue import promote
from tools.validate_question_bank import validate_question_bank


@pytest.fixture
def valid_question():
    return {
        "id": "AUTO_model_history_test",
        "source": "gemini_content_factory",
        "created_at": "2026-10-18T00:00:00Z",
        "bank_version": current_bank_version(),
        "domain": "技術分野",
        "chapter_group": "機械学習",
        "chapter_id": "モデルの選択・評価",
        "difficulty": "standard",
        "question": "汎化性能を確認する目的として最も適切なものはどれか。",
        "choices": ["未知データに対する性能を見積もるため", "訓練データを暗号化するため", "モデルの重みを固定するため", "入力データを削除するため"],
        "correct_index": 0,
        "explanation": "正解理由は、汎化性能が未知データに対する性能を示し、訓練データだけでは見えない過学習を検出するために重要だからである。不正解理由として、暗号化、重み固定、入力削除はいずれも性能評価の目的ではなく、汎化性能の確認とは異なる。未知の入力に対して実運用で予測できることが評価の目的であり、学習時の成績だけでは判断できない。",
        "syllabus": "test_syllabus",
        "provenance": {
            "model": "gemini-3.5-flash",
            "prompt_version": "test_prompt_v1",
            "generated_at": "2026-10-18T00:00:00Z",
            "validator_score": 100,
            "validator_reasons": [],
            "review_score": 100,
            "review_reasons": [],
            "accepted_with_review_warnings": False,
            "choice_shuffle_seed": "test_seed",
            "syllabus_node": "モデルの選択・評価",
            "concepts": ["汎化性能"],
            "bank_version": current_bank_version(),
        },
    }


def validate_fixture(tmp_path, question):
    path = tmp_path / "bank.jsonl"
    path.write_text(json.dumps(question, ensure_ascii=False) + "\n", encoding="utf-8")
    return validate_question_bank(path)


@pytest.mark.parametrize("model", ["gemini-2.5-pro", "gemini-3.5-flash", "gemini-3.8-flash"])
def test_auto_allows_historical_concrete_models(tmp_path, monkeypatch, valid_question, model):
    monkeypatch.setenv("GEMINI_MODEL", "auto")
    valid_question["provenance"]["model"] = model
    assert validate_fixture(tmp_path, valid_question) == []


@pytest.mark.parametrize("model", [None, 42, "", " ", "auto", " AUTO "])
def test_auto_rejects_missing_or_unresolved_model(tmp_path, monkeypatch, valid_question, model):
    monkeypatch.setenv("GEMINI_MODEL", "auto")
    valid_question["provenance"]["model"] = model
    errors = validate_fixture(tmp_path, valid_question)
    assert len(errors) == 1
    assert "provenance.model must contain a concrete model ID" in errors[0]


def test_auto_still_requires_model_provenance(tmp_path, monkeypatch, valid_question):
    monkeypatch.setenv("GEMINI_MODEL", "auto")
    valid_question["provenance"].pop("model")
    errors = validate_fixture(tmp_path, valid_question)
    assert any("missing provenance field model" in error for error in errors)
    assert any("concrete model ID" in error for error in errors)


def test_fixed_model_preserves_existing_exact_match_requirement(tmp_path, monkeypatch, valid_question):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    errors = validate_fixture(tmp_path, valid_question)
    assert len(errors) == 1
    assert "provenance.model must be gemini-3.8-flash" in errors[0]
    valid_question["provenance"]["model"] = "gemini-3.8-flash"
    assert validate_fixture(tmp_path, valid_question) == []


def test_auto_does_not_weaken_other_quality_requirements(tmp_path, monkeypatch, valid_question):
    monkeypatch.setenv("GEMINI_MODEL", "auto")
    valid_question["provenance"]["review_reasons"] = ["needs source review"]
    assert any("review_reasons must be empty" in error for error in validate_fixture(tmp_path, valid_question))


def promotion_paths(tmp_path, question, *, model="gemini-3.5-flash", bank_version=None):
    candidate = {
        **question,
        "syllabus_node": question["chapter_id"],
        "concepts": question["provenance"]["concepts"],
    }
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps({
        "candidate": candidate,
        "target": {"chapter_group": question["chapter_group"], "chapter_id": question["chapter_id"]},
        "model": model,
        "bank_version": current_bank_version() if bank_version is None else bank_version,
        "prompt_version": "test_prompt",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"queue_path": queue, "bank_path": tmp_path / "bank.jsonl", "provenance_path": tmp_path / "provenance.jsonl"}


def test_auto_promotion_preserves_actual_historical_model(tmp_path, monkeypatch, valid_question):
    monkeypatch.setenv("GEMINI_MODEL", "auto")
    paths = promotion_paths(tmp_path, valid_question)
    record = promote(0, **paths)
    assert record["provenance"]["model"] == "gemini-3.5-flash"
    assert validate_question_bank(paths["bank_path"]) == []


@pytest.mark.parametrize("model", [None, 42, "", " ", "auto", " AUTO "])
def test_auto_promotion_rejects_unresolved_model_before_writing(tmp_path, monkeypatch, valid_question, model):
    monkeypatch.setenv("GEMINI_MODEL", "auto")
    paths = promotion_paths(tmp_path, valid_question, model=model)
    with pytest.raises(ValueError, match="concrete generation model ID"):
        promote(0, **paths)
    assert not paths["bank_path"].exists()
    assert not paths["provenance_path"].exists()


def test_fixed_model_promotion_preserves_exact_match_requirement(tmp_path, monkeypatch, valid_question):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    paths = promotion_paths(tmp_path, valid_question)
    with pytest.raises(ValueError, match="model/bank version does not match"):
        promote(0, **paths)
    assert not paths["bank_path"].exists()


def test_auto_promotion_still_rejects_wrong_bank_version(tmp_path, monkeypatch, valid_question):
    monkeypatch.setenv("GEMINI_MODEL", "auto")
    paths = promotion_paths(tmp_path, valid_question, bank_version="other_bank")
    with pytest.raises(ValueError, match="model/bank version does not match"):
        promote(0, **paths)
    assert not paths["bank_path"].exists()
