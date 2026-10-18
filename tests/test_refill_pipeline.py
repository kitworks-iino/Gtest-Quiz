import pytest

from gtest_quiz import refill_pipeline
from gtest_quiz.content_factory import FactoryStats
from gtest_quiz.refill_pipeline import RefillConfig, RefillStats


def test_safe_parse_json_accepts_wrapped_model_text():
    data = refill_pipeline._safe_parse_json("prefix {\"question\":\"q\",\"correct_index\":1} suffix")
    assert data == {"question": "q", "correct_index": 1}


def test_refill_stats_wrap_factory_stats():
    factory_stats = FactoryStats(
        generated=3,
        accepted=2,
        queued_for_review=1,
        rejected=4,
        duplicates=5,
        errors=6,
        api_call_count=7,
        rate_limit_errors=1,
        correct_index_distribution_generated={"0": 0, "1": 2, "2": 0, "3": 0},
    )
    stats = RefillStats.from_factory(factory_stats, config=RefillConfig(mode="daily"))
    assert stats.generated == 3
    assert stats.accepted == 2
    assert stats.queued_for_review == 1
    assert stats.rejected == 4
    assert stats.duplicates == 5
    assert stats.errors == 6
    assert stats.api_call_count == 7
    assert stats.rate_limit_errors == 1
    assert stats.correct_index_distribution_generated == {"0": 0, "1": 2, "2": 0, "3": 0}
    assert stats.model_name == "gemini-3.5-flash"
    assert stats.bank_version == "gemini35_v1"


def test_run_refill_stops_before_generation_when_quota_is_near(monkeypatch):
    class FakeQuota:
        def is_near_limit(self, ratio):
            assert ratio == 0.9
            return True

    class FakeMeta:
        quota = FakeQuota()

        def __init__(self, path):
            self.path = path

        def load(self):
            return None

    monkeypatch.setattr(refill_pipeline, "load_dotenv", lambda: None)
    monkeypatch.setattr(refill_pipeline, "get_env", lambda name: "test-key")
    monkeypatch.setattr(refill_pipeline, "MetaManager", FakeMeta)

    stats = refill_pipeline.run_refill(RefillConfig(target_daily=1))
    assert stats.generated == 0
    assert stats.accepted == 0


def test_run_refill_requires_api_key(monkeypatch):
    monkeypatch.setattr(refill_pipeline, "load_dotenv", lambda: None)
    monkeypatch.setattr(refill_pipeline, "get_env", lambda name: "")

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        refill_pipeline.run_refill(RefillConfig(target_daily=1))


def test_generator_uses_current_structured_sdk(monkeypatch):
    from types import SimpleNamespace
    captured = {}
    class Models:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(parsed={"question": "question"})
    monkeypatch.setattr(refill_pipeline, "_load_generator", lambda key: SimpleNamespace(models=Models()))
    generator = refill_pipeline.GeminiQuestionGenerator(api_key="test", model_name="test-model")
    result = generator.generate("test prompt", refill_pipeline.GeneratedQuestionSpec)
    assert result == {"question": "question"}
    assert captured["config"]["response_schema"] is refill_pipeline.GeneratedQuestionSpec
    assert captured["model"] == "test-model"
    assert generator.api_call_count == 1


def test_generator_counts_quota_errors_without_sleep_after_last_try(monkeypatch):
    from types import SimpleNamespace
    class Models:
        def generate_content(self, **kwargs):
            raise RuntimeError("429 resource exhausted")
    sleeps = []
    monkeypatch.setattr(refill_pipeline, "_load_generator", lambda key: SimpleNamespace(models=Models()))
    monkeypatch.setattr(refill_pipeline.time, "sleep", sleeps.append)
    generator = refill_pipeline.GeminiQuestionGenerator(api_key="test", model_name="test", max_retry=3, sleep_seconds=2)
    with pytest.raises(RuntimeError, match="429"):
        generator.generate("prompt", refill_pipeline.GeneratedQuestionSpec)
    assert generator.api_call_count == generator.rate_limit_errors == 3
    assert sleeps == [2, 2]


def test_generator_does_not_retry_bad_credentials(monkeypatch):
    from types import SimpleNamespace
    class Forbidden(Exception):
        code = 403
    class Models:
        def generate_content(self, **kwargs):
            raise Forbidden("not allowed")
    monkeypatch.setattr(refill_pipeline, "_load_generator", lambda key: SimpleNamespace(models=Models()))
    generator = refill_pipeline.GeminiQuestionGenerator(api_key="test", model_name="test")
    with pytest.raises(Forbidden):
        generator.generate("prompt", refill_pipeline.GeneratedQuestionSpec)
    assert generator.api_call_count == 1


@pytest.mark.parametrize("raw", ["not json", "prefix {invalid} suffix", ""])
def test_safe_parse_json_rejects_invalid_response(raw):
    assert refill_pipeline._safe_parse_json(raw) is None


def _refill_fixture(tmp_path, monkeypatch, *, fail=False):
    import json
    from test_content_factory import StaticGenerator
    from gtest_quiz.meta import MetaManager
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(refill_pipeline, "load_dotenv", lambda: None)
    monkeypatch.setattr(refill_pipeline, "get_env", lambda key: "test-key")
    class Generator(StaticGenerator):
        api_call_count = 0
        rate_limit_errors = 0
        def generate(self, prompt, schema):
            self.api_call_count += 1
            if fail:
                self.rate_limit_errors += 1
                raise RuntimeError("429 quota exceeded")
            return super().generate(prompt, schema)
    monkeypatch.setattr(refill_pipeline, "GeminiQuestionGenerator", lambda **kwargs: Generator())
    meta = MetaManager()
    meta.load()
    meta.meta["chapters"] = {"group": {"label": "機械学習", "subchapters": {"chapter": {"label": "評価", "weight": 1}}}}
    meta.meta["sentinel"] = {"preserve": True}
    meta.save()
    bank = tmp_path / "bank/question_bank.jsonl"
    bank.write_text(json.dumps({"id": "old", "question": "旧問題", "correct_index": 0}) + "\n")
    return bank


def test_reset_stages_and_validates_before_replacing_bank(tmp_path, monkeypatch):
    import json
    bank = _refill_fixture(tmp_path, monkeypatch)
    stats = refill_pipeline.run_refill(RefillConfig(target_daily=1, mode="reset_and_seed"))
    assert stats.reset_performed
    assert stats.accepted == 1
    assert stats.deleted_question_count == 1
    assert json.loads(bank.read_text())["id"] != "old"
    assert not (tmp_path / "bank/question_bank.next.jsonl").exists()
    meta = json.loads((tmp_path / "bank/meta.json").read_text())
    assert meta["sentinel"] == {"preserve": True}
    assert meta["last_refill_result"]["api_call_count"] == 1


def test_quota_failure_preserves_old_bank_and_stops_factory_calls(tmp_path, monkeypatch):
    bank = _refill_fixture(tmp_path, monkeypatch, fail=True)
    original = bank.read_bytes()
    stats = refill_pipeline.run_refill(RefillConfig(target_daily=5, mode="reset_and_seed"))
    assert not stats.reset_performed
    assert stats.accepted == 0
    assert stats.api_call_count == stats.rate_limit_errors == 1
    assert bank.read_bytes() == original


def test_bad_staged_bank_cannot_replace_live_bank(tmp_path, monkeypatch):
    bank = _refill_fixture(tmp_path, monkeypatch)
    original = bank.read_bytes()
    (tmp_path / "bank/question_bank.next.jsonl").write_text('{"invalid": true}\n')
    with pytest.raises(RuntimeError, match="validation failed"):
        refill_pipeline._swap_next_bank(config=RefillConfig(), meta_path="bank/meta.json", stats=RefillStats(), before_count=1)
    assert bank.read_bytes() == original


def test_replacement_drops_only_successfully_generated_chapter(tmp_path):
    import json
    old_a = {"id": "a", "chapter_group": "group", "chapter_id": "a"}
    old_b = {"id": "b", "chapter_group": "group", "chapter_id": "b"}
    new_b = {"id": "new-b", "chapter_group": "group", "chapter_id": "b"}
    bank, provenance = tmp_path / "staged.jsonl", tmp_path / "provenance.jsonl"
    bank.write_text("".join(json.dumps(row) + "\n" for row in [old_a, old_b, new_b]))
    provenance.write_text("".join(json.dumps({"id": row["id"]}) + "\n" for row in [old_a, old_b, new_b]))
    removed = refill_pipeline._drop_replaced_items([old_a, old_b], [new_b], bank_path=bank, provenance_path=provenance)
    assert removed == 1
    assert [row["id"] for row in refill_pipeline.load_existing_items(bank)] == ["a", "new-b"]
    assert [row["id"] for row in refill_pipeline.load_existing_items(provenance)] == ["a", "new-b"]


def test_failed_replacement_cannot_publish_partly_invalid_bank(tmp_path, monkeypatch):
    bank = _refill_fixture(tmp_path, monkeypatch)
    original = bank.read_bytes()
    with pytest.raises(RuntimeError, match="validation failed"):
        refill_pipeline.run_refill(RefillConfig(target_daily=1, mode="replace"))
    assert bank.read_bytes() == original
