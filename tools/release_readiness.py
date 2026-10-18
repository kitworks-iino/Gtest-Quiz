"""Release checks for the static PWA and questions actually available to learn.

This is one CI gate; pytest coverage, Node tests and bank validation remain required.
No server, accounts, network request or paid model is started by these checks.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_static_pwa_assets import SHELL_FILES, load_questions, load_bank_version, _canonical_content
from tools.generate_coverage_report import PROFILE_RULES, build_coverage_report, difficulty_balance_ok
from tools.validate_question_bank import validate_question_bank


def _check(name: str, passed: bool, **detail: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def check_learning_app() -> dict[str, Any]:
    """Run the actual shipped JS modules against the actual usable problem bank."""
    try:
        result = subprocess.run(
            ["node", str(ROOT / "tools/check_learning_app.mjs")],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode:
            return {"passed": False, "error": result.stderr[-3000:]}
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {"passed": False, "error": str(error)}


def evaluate_release_readiness(profile: str | None = None) -> dict[str, Any]:
    profile = (profile or os.getenv("READINESS_PROFILE", "complete")).strip().lower()
    if profile == "production":
        profile = "complete"
    if profile not in PROFILE_RULES:
        raise ValueError(f"unknown readiness profile: {profile}")
    rules = PROFILE_RULES[profile]
    coverage = build_coverage_report(
        bank_path=ROOT / "bank/question_bank.jsonl", meta_path=ROOT / "bank/meta.json",
        review_queue_path=ROOT / "bank/generated_review_queue.jsonl",
    )
    errors = validate_question_bank(ROOT / "bank/question_bank.jsonl")
    app = check_learning_app()
    chapters = app.get("chapters", [])
    available = app.get("availableCount", 0)
    expected_ids = {chapter["chapter_id"] for chapter in coverage["chapters"]}
    usable_by_id = {chapter["id"]: chapter for chapter in chapters}
    covered = len(expected_ids & usable_by_id.keys())
    floor = rules["min_per_chapter"]
    below_floor = sorted(chapter for chapter in expected_ids
                         if usable_by_id.get(chapter, {}).get("total", 0) < floor) if floor else []
    difficulty_failures = [chapter["id"] for chapter in chapters
                          if not difficulty_balance_ok(chapter["difficulty"], floor)]
    frontend = ROOT / "frontend/src"
    missing = [name for name in (*SHELL_FILES, "service-worker.js", ".nojekyll")
               if not (frontend / name).is_file()]
    questions = load_questions()
    static_bank = json.loads((frontend / "question-bank.json").read_text(encoding="utf-8"))
    content_hash = hashlib.sha256(_canonical_content(questions, load_bank_version()).encode()).hexdigest()
    checks = [
        _check("question_bank_valid", not errors, errors=errors[:5]),
        _check("coverage_report_quality", coverage["profiles"][profile]["passed"],
               source_questions=coverage["total_questions"], profile=profile),
        _check("readiness_profile_question_count", available >= rules["min_questions"],
               usable_questions=available, held=app.get("heldCount"), minimum=rules["min_questions"]),
        _check("syllabus_coverage", covered >= rules["min_chapters"] and not below_floor,
               covered=covered, minimum=rules["min_chapters"], below_floor=below_floor),
        _check("usable_difficulty_balance", not difficulty_failures, chapters=difficulty_failures),
        _check("learning_app_smoke", app.get("passed", False), result=app),
        _check("offline_shell_complete", not missing, missing=missing),
        _check("static_bank_matches_source", static_bank.get("questions") == questions
               and static_bank.get("schema_version") == "gtest_quiz_static_bank_v1"
               and static_bank.get("meta", {}).get("question_count") == len(questions)
               and static_bank.get("meta", {}).get("content_hash") == content_hash),
    ]
    return {"passed": all(check["passed"] for check in checks), "profile": profile, "checks": checks}


def main() -> None:
    result = evaluate_release_readiness()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
