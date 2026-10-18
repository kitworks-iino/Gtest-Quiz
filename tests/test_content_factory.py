import json
import re
from pathlib import Path

from gtest_quiz.content_factory import (
    ContentFactory,
    CoverageTarget,
    FactoryConfig,
    GeneratedQuestionSpec,
    build_coverage_targets,
    decide_candidate,
    shuffle_choices_for_record,
)
from gtest_quiz.meta import MetaManager
from gtest_quiz.question_quality import build_duplicate_index
from tools.review_generated_queue import summarize_queue


class StaticGenerator:
    def __init__(self) -> None:
        self.count = 0

    def generate(self, prompt: str, schema: type[GeneratedQuestionSpec]):
        self.count += 1
        difficulty_match = re.search(r"Target difficulty:\s*(basic|standard|advanced)", prompt)
        difficulty = difficulty_match.group(1) if difficulty_match else "standard"
        return {
            "question": f"汎化性能を確認する目的として最も適切なものはどれか。{self.count}",
            "choices": [
                "未知データに対する性能を見積もるため",
                "訓練データを暗号化するため",
                "モデルの重みを固定するため",
                "入力データを削除するため",
            ],
            "correct_index": 0,
            "explanation": "正解理由は、汎化性能が未知データに対する性能を示し、訓練データだけでは見えない過学習を検出するために重要だからである。不正解理由として、暗号化、重み固定、入力削除はいずれも性能評価の目的ではなく、汎化性能の確認とは異なる。未知の入力に対して実運用で予測できることが評価の目的であり、学習時の成績だけでは判断できない。",
            "difficulty": difficulty,
            "syllabus_node": "10. モデルの選択・評価",
            "concepts": ["汎化性能", "評価"],
            "source_hint": "test",
        }


def test_decide_candidate_routes_duplicates():
    data = {
        "question": "ニューラルネットワークにおける活性化関数の役割は何か。",
        "choices": ["線形性を維持する", "非線形性を導入する", "計算量を削減する", "重みを初期化する"],
        "correct_index": 1,
        "explanation": "正解理由は、活性化関数が非線形性を導入し、モデルが複雑な関係を学習できるようにするためである。不正解理由として、線形性維持、計算量削減、重み初期化は活性化関数の主目的ではない。",
        "difficulty": "standard",
    }
    duplicate_index = build_duplicate_index([data])
    decision = decide_candidate(data, duplicate_index=duplicate_index, min_explanation_length=40, review_threshold=75)
    assert decision.action == "duplicate"


def test_coverage_targets_prioritize_deficits(tmp_path: Path):
    meta_path = tmp_path / "meta.json"
    meta = MetaManager(str(meta_path))
    meta.load()
    meta.meta["chapters"] = {
        "g": {
            "label": "group",
            "subchapters": {
                "a": {"label": "A", "weight": 1.0},
                "b": {"label": "B", "weight": 1.0},
            },
        }
    }
    targets = build_coverage_targets(meta, [{"chapter_id": "A"}, {"chapter_id": "A"}], per_chapter_floor=3)
    assert targets[0].chapter_id == "B"
    assert targets[0].priority > targets[1].priority
    assert targets[0].desired_difficulty in {"basic", "standard", "advanced"}


def test_choice_shuffle_recomputes_correct_index():
    choices = ["correct", "wrong-a", "wrong-b", "wrong-c"]
    shuffled, correct_index, seed = shuffle_choices_for_record(
        choices,
        0,
        seed_material="unit-test",
        desired_correct_index=3,
    )

    assert shuffled[correct_index] == "correct"
    assert correct_index == 3
    assert seed
    assert set(shuffled) == set(choices)


def test_coverage_targets_continue_after_floor_is_met(tmp_path: Path):
    meta_path = tmp_path / "meta.json"
    meta = MetaManager(str(meta_path))
    meta.load()
    meta.meta["chapters"] = {
        "g": {
            "label": "group",
            "subchapters": {
                "a": {"label": "A", "weight": 1.0},
                "b": {"label": "B", "weight": 1.0},
            },
        }
    }
    existing = [{"chapter_id": "A"} for _ in range(10)] + [{"chapter_id": "B"} for _ in range(10)]

    targets = build_coverage_targets(meta, existing, per_chapter_floor=10)

    assert {target.chapter_id for target in targets} == {"A", "B"}
    assert all(target.priority > 0 for target in targets)
    assert all(target.target_count == 11 for target in targets)


def test_content_factory_accepts_and_writes_provenance(tmp_path: Path):
    bank = tmp_path / "question_bank.jsonl"
    review = tmp_path / "review.jsonl"
    provenance = tmp_path / "provenance.jsonl"
    meta_path = tmp_path / "meta.json"
    meta = MetaManager(str(meta_path))
    meta.load()
    meta.meta["chapters"] = {
        "g": {
            "label": "機械学習の概要",
            "subchapters": {"m": {"label": "10. モデルの選択・評価", "weight": 1.0}},
        }
    }
    meta.save()

    factory = ContentFactory(
        FactoryConfig(
            model_name="fixture-resolved-model",
            target_accepts=1,
            question_bank_path=bank,
            review_queue_path=review,
            provenance_path=provenance,
            min_explanation_length=40,
        ),
        StaticGenerator(),
    )
    stats = factory.run(meta_path=str(meta_path))
    assert stats.accepted == 1
    assert sum(stats.correct_index_distribution_generated.values()) == 1
    written = json.loads(bank.read_text(encoding="utf-8").splitlines()[0])
    assert written["domain"] == "技術分野"
    assert written["bank_version"] == "gemini35_v1"
    assert written["provenance"]["prompt_version"]
    assert written["provenance"]["model"] == "fixture-resolved-model"
    assert written["provenance"]["bank_version"] == "gemini35_v1"
    assert written["provenance"]["validator_score"] >= 75
    assert written["provenance"]["review_score"] >= 95
    assert written["provenance"]["review_reasons"] == []
    assert written["provenance"]["accepted_with_review_warnings"] is False
    assert written["provenance"]["choice_shuffle_seed"]
    assert 0 <= written["correct_index"] <= 3
    assert provenance.exists()
    updated_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert updated_meta["bank_version"] == "gemini35_v1"
    assert updated_meta["content_factory"]["accepted"] == 1
    assert sum(updated_meta["content_factory"]["correct_index_distribution_generated"].values()) == 1
    assert updated_meta["question_bank"]["total_questions"] == 1
    assert written["provenance"]["desired_difficulty"] == "standard"


def test_content_factory_adds_daily_items_after_all_chapters_are_full(tmp_path: Path):
    bank = tmp_path / "question_bank.jsonl"
    review = tmp_path / "review.jsonl"
    provenance = tmp_path / "provenance.jsonl"
    meta_path = tmp_path / "meta.json"
    meta = MetaManager(str(meta_path))
    meta.load()
    meta.meta["chapters"] = {
        "g": {
            "label": "機械学習の概要",
            "subchapters": {
                "a": {"label": "10. モデルの選択・評価", "weight": 1.0},
                "b": {"label": "11. ニューラルネットワーク", "weight": 1.0},
            },
        }
    }
    meta.save()
    existing = []
    for chapter_id in ("10. モデルの選択・評価", "11. ニューラルネットワーク"):
        for i in range(10):
            existing.append(
                {
                    "id": f"seed_{chapter_id}_{i}",
                    "chapter_id": chapter_id,
                    "question": f"{chapter_id} 既存問題 {i}",
                    "choices": ["a", "b", "c", "d"],
                    "correct_index": len(existing) % 4,
                    "explanation": "既存問題の解説です。",
                }
            )
    bank.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in existing) + "\n", encoding="utf-8")

    factory = ContentFactory(
        FactoryConfig(
            target_accepts=2,
            question_bank_path=bank,
            review_queue_path=review,
            provenance_path=provenance,
            min_explanation_length=40,
        ),
        StaticGenerator(),
    )

    stats = factory.run(meta_path=str(meta_path))
    lines = bank.read_text(encoding="utf-8").splitlines()

    assert stats.generated >= 2
    assert stats.accepted == 2
    assert len(lines) == 22
    updated_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert updated_meta["content_factory"]["accepted"] == 2
    assert updated_meta["question_bank"]["total_questions"] == 22


def test_review_queue_summary(tmp_path: Path):
    queue = tmp_path / "review.jsonl"
    queue.write_text(
        "\n".join(
            [
                json.dumps({"decision": "review"}, ensure_ascii=False),
                json.dumps({"decision": "reject"}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    summary = summarize_queue(queue)
    assert summary["total"] == 2
    assert summary["by_decision"] == {"reject": 1, "review": 1}
    assert summary["review_queue_total"] == 2


def _run_factory(tmp_path, generator, *, existing=(), target=5):
    bank, review, provenance = (tmp_path / name for name in ("bank.jsonl", "review.jsonl", "provenance.jsonl"))
    bank.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in existing), encoding="utf-8")
    config = FactoryConfig(
        target_accepts=target, max_attempts_per_target=1,
        explicit_targets=[CoverageTarget("機械学習の概要", "評価", 0, target, 1)],
        question_bank_path=bank, review_queue_path=review, provenance_path=provenance,
    )
    stats = ContentFactory(config, generator).run(str(tmp_path / "meta.json"))
    return stats, bank, review, provenance


def _existing_distribution():
    # The production incident: a 19-row tail passed the old size cutoff but
    # random additions triggered the unchanged 20+ row distribution gate.
    positions = [index % 4 for index in range(900)] + [0] * 4 + [1] + [2] * 5 + [3] * 9
    return [{"id": f"existing-{index}", "question": f"既存の別問題{index}", "correct_index": position} for index, position in enumerate(positions)]


def test_daily_five_repairs_tail_distribution_without_rewriting_existing(tmp_path):
    from tools.validate_question_bank import _distribution_errors
    existing = _existing_distribution()
    stats, bank, _, provenance = _run_factory(tmp_path, StaticGenerator(), existing=existing)
    rows = [json.loads(line) for line in bank.read_text().splitlines()]
    assert stats.accepted == 5
    assert rows[:len(existing)] == existing
    assert _distribution_errors(rows, bank) == []
    assert len(provenance.read_text().splitlines()) == 5
    assert all(row["choices"][row["correct_index"]] == "未知データに対する性能を見積もるため" for row in rows[len(existing):])


def test_partial_batch_never_appends_invalid_distribution(tmp_path):
    existing = _existing_distribution()
    stats, bank, review, provenance = _run_factory(tmp_path, StaticGenerator(), existing=existing, target=1)
    assert stats.accepted == 0
    assert stats.queued_for_review == 1
    assert [json.loads(line) for line in bank.read_text().splitlines()] == existing
    assert not provenance.exists()
    assert "distribution" in review.read_text()


def test_answer_positions_stay_balanced_across_window_boundary():
    from gtest_quiz.content_factory import BalancedAnswerPositions
    from gtest_quiz.question_quality import correct_index_distribution_errors
    rows = [{"correct_index": index % 4} for index in range(99)]
    positions = BalancedAnswerPositions(rows)
    rows.extend({"correct_index": positions.take()} for _ in range(125))
    assert correct_index_distribution_errors(rows) == []


def test_numeric_explanations_are_held_without_being_shuffled(tmp_path):
    class NumberedGenerator(StaticGenerator):
        def generate(self, prompt, schema):
            data = super().generate(prompt, schema)
            data["explanation"] += "正解は選択肢1です。"
            return data
    stats, bank, review, provenance = _run_factory(tmp_path, NumberedGenerator(), target=1)
    assert stats.accepted == 0
    assert not bank.read_text()
    assert not provenance.exists()
    assert "choice position references" in review.read_text()


def test_malformed_response_is_rejected_before_record_creation(tmp_path):
    class MalformedGenerator:
        def generate(self, prompt, schema):
            return {"question": "不完全な生成レスポンスです。", "choices": None, "correct_index": True}
    stats, bank, review, provenance = _run_factory(tmp_path, MalformedGenerator(), target=1)
    assert stats.rejected > 0
    assert stats.accepted == 0
    assert bank.read_text() == ""
    assert not provenance.exists()
    assert "schema:" in review.read_text()


def test_review_promotion_uses_same_gates_and_rejects_duplicate(tmp_path):
    import pytest
    from tools.review_generated_queue import promote
    generator = StaticGenerator()
    candidate = generator.generate("Target difficulty: standard", GeneratedQuestionSpec)
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps({
        "candidate": candidate, "target": {"chapter_group": "機械学習の概要", "chapter_id": "評価"},
        "model": "gemini-3.5-flash", "bank_version": "gemini35_v1", "prompt_version": "test",
    }, ensure_ascii=False) + "\n")
    bank, provenance = tmp_path / "bank.jsonl", tmp_path / "provenance.jsonl"
    record = promote(0, queue_path=queue, bank_path=bank, provenance_path=provenance)
    assert record["choices"][record["correct_index"]] == candidate["choices"][candidate["correct_index"]]
    with pytest.raises(ValueError, match="duplicate"):
        promote(0, queue_path=queue, bank_path=bank, provenance_path=provenance)
    assert len(bank.read_text().splitlines()) == 1


def test_corrupt_existing_bank_is_not_silently_skipped(tmp_path):
    import pytest
    from gtest_quiz.content_factory import load_existing_items
    bank = tmp_path / "corrupt.jsonl"
    for raw in ("not json\n", "[]\n"):
        bank.write_text(raw)
        with pytest.raises(ValueError, match="corrupt.jsonl:1"):
            load_existing_items(bank)
