import json
import re
from html.parser import HTMLParser
from pathlib import Path

from gtest_quiz.content_factory import LEGAL_CHAPTER_GROUPS, LEGAL_SOURCE_FIELDS, domain_for_chapter_group
from tools import build_static_pwa_assets


class _PageAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_frontend_page_loads_app_modules_and_install_metadata():
    text = Path("frontend/src/index.html").read_text(encoding="utf-8")
    page = _PageAssets()
    page.feed(text)
    assert any(tag == "html" and attrs.get("lang") == "ja" for tag, attrs in page.tags)
    assert any(tag == "script" and attrs.get("src") == "./offline-app.js"
               and attrs.get("type") == "module" for tag, attrs in page.tags)
    for relation, href in [("manifest", "./manifest.webmanifest"),
                           ("stylesheet", "./styles.css"),
                           ("apple-touch-icon", "./pwa-icon-180.png")]:
        assert any(tag == "link" and attrs.get("rel") == relation and attrs.get("href") == href
                   for tag, attrs in page.tags)
    assert {"app", "screen", "navigation"} <= {attrs.get("id") for _, attrs in page.tags}
    assert any(tag == "meta" and attrs.get("name") == "apple-mobile-web-app-capable"
               and attrs.get("content") == "yes" for tag, attrs in page.tags)
    assert "authForm" not in text
    for tag, attrs in page.tags:
        if tag == "script" and "src" in attrs:
            assert not attrs["src"].startswith(("http:", "https:", "//"))


def test_frontend_uses_local_learning_engine_and_bank():
    html = Path("frontend/src/index.html").read_text(encoding="utf-8")
    script = Path("frontend/src/offline-app.js").read_text(encoding="utf-8")
    engine = Path("frontend/src/learning-engine.js").read_text(encoding="utf-8")
    client_source = html + script + engine
    for forbidden in ("http://localhost", "/api/v1", "/auth/login", "/auth/register"):
        assert forbidden not in client_source
    assert "./learning-engine.js" in script
    assert "createEngine" in script
    assert "export function createEngine" in engine
    assert "STATIC_BANK_URL" in script
    assert "./question-bank.json" in script
    assert re.search(r"fetch\(\s*STATIC_BANK_URL\s*,\s*\{\s*cache:\s*['\"]no-store['\"]\s*\}\s*\)", script)
    assert "localStorage" in engine
    assert "gtestQuizOfflineStateV1" in engine
    for capability in ("buildSession", "answer", "exportData", "importData"):
        assert capability in script
        assert capability in engine
    assert "updateSchedule" in engine
    assert "gtest_quiz_offline_export_v1" in engine


def test_static_question_bank_asset_matches_source_bank():
    bank = json.loads(Path("frontend/src/question-bank.json").read_text(encoding="utf-8"))
    source_count = len(Path("bank/question_bank.jsonl").read_text(encoding="utf-8").splitlines())
    assert bank["schema_version"] == "gtest_quiz_static_bank_v1"
    assert bank["meta"]["question_count"] == source_count
    assert len(bank["questions"]) == source_count
    assert bank["meta"]["bank_version"] == "gemini35_v1"
    assert bank["meta"]["content_hash"]
    assert "coverage" in bank["meta"]
    assert "generated_at" not in bank["meta"]
    assert "git_commit" not in bank["meta"]
    if bank["questions"]:
        first = bank["questions"][0]
        assert {"id", "question", "choices", "correct_index", "explanation", "chapter_id", "bank_version"} <= set(first)
        assert len(first["choices"]) == 4


def test_static_question_bank_keeps_legal_sources(tmp_path, monkeypatch):
    group = next(iter(LEGAL_CHAPTER_GROUPS))
    row = {
        "id": "legal-source-test",
        "bank_version": "gemini35_v1",
        "domain": domain_for_chapter_group(group),
        "chapter_group": group,
        "chapter_id": "legal chapter",
        "difficulty": "standard",
        "question": "legal source test",
        "choices": ["a", "b", "c", "d"],
        "correct_index": 0,
        "explanation": "legal source explanation",
        "syllabus": "G2024_v1.3",
        **{field: f"{field}-value" for field in LEGAL_SOURCE_FIELDS},
    }
    bank_path = tmp_path / "question_bank.jsonl"
    meta_path = tmp_path / "meta.json"
    bank_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    meta_path.write_text(json.dumps({"bank_version": "gemini35_v1"}), encoding="utf-8")

    monkeypatch.setattr(build_static_pwa_assets, "BANK_PATH", bank_path)
    monkeypatch.setattr(build_static_pwa_assets, "META_PATH", meta_path)

    [item] = build_static_pwa_assets.load_questions()
    for field in LEGAL_SOURCE_FIELDS:
        assert item[field] == f"{field}-value"


def test_frontend_pwa_shell_is_complete_and_matches_build_inputs():
    frontend = Path("frontend/src")
    manifest_data = json.loads((frontend / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest_data["display"] == "standalone"
    assert manifest_data["start_url"] == "./index.html"
    assert manifest_data["scope"] == "./"
    assert manifest_data["short_name"] == "Gtest"
    assert manifest_data["theme_color"] == "#08090a"
    assert any(icon["src"] == "./pwa-icon-512.png" for icon in manifest_data["icons"])

    worker_text = (frontend / "service-worker.js").read_text(encoding="utf-8")
    shell_block = worker_text.split("const SHELL = [", 1)[1].split("];", 1)[0]
    shell_files = tuple(path.removeprefix("./") for path in re.findall(r"'([^']+)'", shell_block))
    assert shell_files == build_static_pwa_assets.SHELL_FILES
    for filename in shell_files:
        assert (frontend / filename).is_file(), f"Missing offline shell asset: {filename}"
    assert "question-bank.json" in shell_files
    assert "question-review-holds.json" in shell_files
    assert "QUESTION_BANK_URL" in worker_text
    assert "questionBankNetworkFirst(request)" in worker_text
    assert "cache: 'no-store'" in worker_text
    assert "gtest-quiz-static-" in worker_text
