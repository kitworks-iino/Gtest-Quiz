import json
from types import SimpleNamespace

import pytest

from tools import auto_refill_quality


def test_resolve_only_does_not_generate_or_write_a_bank(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["auto_refill_quality.py", "--mode", "resolve_model_only"])
    monkeypatch.setattr(auto_refill_quality, "load_dotenv", lambda: None)
    monkeypatch.setattr(auto_refill_quality, "get_env", lambda name: "test-key")
    monkeypatch.setattr(auto_refill_quality, "current_model_name", lambda: "auto")
    selection = {"model_id": "gemini-fixture", "eligible_models": ["gemini-fixture"]}

    def generator(**kwargs):
        assert kwargs == {"api_key": "test-key", "model_name": "auto"}
        return SimpleNamespace(model_selection=selection)

    def unexpected(*args, **kwargs):
        pytest.fail("Read-only model selection must not generate or write reports")

    monkeypatch.setattr(auto_refill_quality, "GeminiQuestionGenerator", generator)
    monkeypatch.setattr(auto_refill_quality, "run_refill", unexpected)
    monkeypatch.setattr(auto_refill_quality, "write_coverage_report", unexpected)
    auto_refill_quality.main()
    result = json.loads(capsys.readouterr().out)
    assert result["model_selection"] == selection
    assert result["api_call_count"] == 0
    assert list(tmp_path.iterdir()) == []


def test_resolve_only_requires_key_without_generating(monkeypatch):
    monkeypatch.setattr("sys.argv", ["auto_refill_quality.py", "--mode", "resolve_model_only"])
    monkeypatch.setattr(auto_refill_quality, "load_dotenv", lambda: None)
    monkeypatch.setattr(auto_refill_quality, "get_env", lambda name: "")
    with pytest.raises(SystemExit, match="GEMINI_API_KEY is not set"):
        auto_refill_quality.main()
