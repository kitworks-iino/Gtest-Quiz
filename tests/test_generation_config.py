"""Configuration and persisted state must survive generation-only cleanup."""

import json

import pytest

from gtest_quiz import env
from gtest_quiz.bank_epoch import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_QUESTION_BANK_EPOCH,
    current_bank_version,
    current_model_name,
)
from gtest_quiz.meta import MetaManager
from gtest_quiz.quota import QuotaManager


def test_dotenv_keeps_actions_secret_and_loads_local_defaults(tmp_path, monkeypatch):
    for key in ("GTEST_CONFIG_TOKEN", "GTEST_CONFIG_EXISTING", "GTEST_CONFIG_QUOTED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GTEST_CONFIG_EXISTING", "actions-secret")
    (tmp_path / ".env").write_text(
        '# local defaults\n\ninvalid line\n=ignored\n'
        'GTEST_CONFIG_EXISTING=local-secret\n'
        'GTEST_CONFIG_TOKEN="value=with=equals"\n'
        "GTEST_CONFIG_QUOTED='quoted-value'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env, "ROOT_DIR", tmp_path)

    assert env.get_env("GTEST_CONFIG_EXISTING") == "actions-secret"
    assert env.get_env("GTEST_CONFIG_TOKEN") == "value=with=equals"
    assert env.get_env("GTEST_CONFIG_QUOTED") == "quoted-value"
    assert env.get_env("GTEST_CONFIG_MISSING", "fallback") == "fallback"


def test_missing_local_env_is_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(env, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("GTEST_CONFIG_MISSING", raising=False)
    assert env.get_env("GTEST_CONFIG_MISSING", "fallback") == "fallback"


def test_bank_identity_defaults_and_explicit_overrides(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("QUESTION_BANK_EPOCH", raising=False)
    assert current_model_name() == DEFAULT_GEMINI_MODEL
    assert current_bank_version() == DEFAULT_QUESTION_BANK_EPOCH
    monkeypatch.setenv("GEMINI_MODEL", "  model-for-test  ")
    monkeypatch.setenv("QUESTION_BANK_EPOCH", "  bank-for-test  ")
    assert current_model_name() == "model-for-test"
    assert current_bank_version() == "bank-for-test"
    monkeypatch.setenv("GEMINI_MODEL", "  ")
    monkeypatch.setenv("QUESTION_BANK_EPOCH", "  ")
    assert current_model_name() == DEFAULT_GEMINI_MODEL
    assert current_bank_version() == DEFAULT_QUESTION_BANK_EPOCH


def test_metadata_update_preserves_chapters_and_existing_history(tmp_path):
    path = tmp_path / "bank" / "meta.json"
    path.parent.mkdir()
    preserved = {
        "chapters": {"group": {"subchapters": {"chapter": {"label": "章"}}}},
        "usage": {"total_questions": 123},
        "last_refill_result": {"accepted": 7},
        "quota_estimate": {"estimated_limit_tokens": 1000, "total_used_tokens": 950},
        "custom_metadata": ["retained"],
    }
    path.write_text(json.dumps(preserved), encoding="utf-8")
    meta = MetaManager(str(path))
    meta.load()
    assert meta.quota.is_near_limit()
    meta.meta["last_refill_result"] = {"accepted": 8}
    meta.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    for field in ("chapters", "usage", "quota_estimate", "custom_metadata"):
        assert saved[field] == preserved[field]
    assert saved["last_refill_result"] == {"accepted": 8}
    assert saved["created_at"] and saved["updated_at"]
    assert not path.with_suffix(".json.tmp").exists()


def test_invalid_metadata_is_rejected_without_overwriting_it(tmp_path):
    path = tmp_path / "meta.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        MetaManager(str(path)).load()
    assert path.read_text(encoding="utf-8") == "[]"


def test_unknown_quota_does_not_pretend_a_confirmed_limit():
    assert not QuotaManager({}).is_near_limit()
    assert not QuotaManager({"quota_estimate": {"estimated_limit_tokens": 0}}).is_near_limit()
    quota = QuotaManager({"quota_estimate": {"estimated_limit_tokens": 100, "total_used_tokens": 89}})
    assert not quota.is_near_limit()
    quota.estimate["total_used_tokens"] = 90
    assert quota.is_near_limit()
