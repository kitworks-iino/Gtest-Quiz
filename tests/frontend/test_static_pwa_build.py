"""Protect offline cache invalidation and authored frontend assets on refill."""
import json
from pathlib import Path

import pytest

from tools import build_static_pwa_assets as build


@pytest.fixture
def static_build(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    for filename in build.SHELL_FILES:
        path = frontend / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"authored {filename}", encoding="utf-8")
    worker = frontend / "service-worker.js"
    worker.write_text("const CACHE_NAME = 'gtest-quiz-static-old';\n// worker logic\n", encoding="utf-8")
    bank_path = tmp_path / "question_bank.jsonl"
    bank_path.write_text(json.dumps({
        "id": "test-question", "bank_version": "test-v1",
        "question": "Question?", "choices": ["a", "b", "c", "d"],
        "correct_index": 2, "explanation": "Reason", "chapter_id": "test chapter",
    }) + "\n", encoding="utf-8")
    meta_path = tmp_path / "meta.json"
    meta_path.write_text(json.dumps({"bank_version": "test-v1"}), encoding="utf-8")
    for name, value in {
        "FRONTEND": frontend,
        "STATIC_BANK_PATH": frontend / "question-bank.json",
        "SERVICE_WORKER_PATH": worker,
        "BANK_PATH": bank_path,
        "META_PATH": meta_path,
        "COVERAGE_REPORT_PATH": tmp_path / "coverage.json",
    }.items():
        monkeypatch.setattr(build, name, value)
    return frontend


def test_rebuilding_same_bank_and_shell_has_no_diff(static_build):
    first = build.write_static_bank()
    before = {p.name: p.read_bytes() for p in static_build.rglob("*") if p.is_file()}
    second = build.write_static_bank()
    after = {p.name: p.read_bytes() for p in static_build.rglob("*") if p.is_file()}
    assert first["cache_name"] == second["cache_name"]
    assert not second["static_bank_changed"]
    assert before == after


@pytest.mark.parametrize("filename", [
    "index.html", "offline-app.js", "styles.css", "learning-engine.js", "content-guard.js", "ui.js",
    "manifest.webmanifest", "assets/InterVariable.woff2", "service-worker.js",
])
def test_ui_only_change_invalidates_offline_cache(static_build, filename):
    first = build.write_static_bank()
    path = static_build / filename
    path.write_bytes(path.read_bytes() + b"\nchanged source")
    second = build.write_static_bank()
    assert first["content_hash"] == second["content_hash"]
    assert first["cache_name"] != second["cache_name"]


def test_bank_content_change_invalidates_cache(static_build):
    first = build.write_static_bank()
    row = json.loads(build.BANK_PATH.read_text(encoding="utf-8"))
    row["explanation"] = "Updated explanation"
    build.BANK_PATH.write_text(json.dumps(row) + "\n", encoding="utf-8")
    second = build.write_static_bank()
    assert first["content_hash"] != second["content_hash"]
    assert first["cache_name"] != second["cache_name"]


def test_refill_build_preserves_all_authored_ui_and_icons(static_build, capsys):
    authored = [path for path in static_build.rglob("*")
                if path.is_file() and path.name not in {"question-bank.json", "service-worker.js"}]
    before = {path: path.read_bytes() for path in authored}
    build.main()
    assert all(path.read_bytes() == contents for path, contents in before.items())
    assert json.loads(capsys.readouterr().out)["questions"] == 1


def test_missing_shell_asset_fails_build(static_build):
    (static_build / "learning-engine.js").unlink()
    with pytest.raises(FileNotFoundError):
        build.write_static_bank()


def test_missing_worker_cache_declaration_fails_build(static_build):
    build.SERVICE_WORKER_PATH.write_text("// invalid worker", encoding="utf-8")
    with pytest.raises(ValueError, match="CACHE_NAME"):
        build.write_static_bank()
