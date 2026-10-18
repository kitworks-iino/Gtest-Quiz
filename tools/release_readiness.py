from __future__ import annotations

import json
import os
import sys
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.experiments import ADAPTIVE_POLICY
from backend.app.services import QuizService, UnauthorizedError
from gtest_quiz.bank_epoch import current_bank_version, current_model_name
from gtest_quiz.question_bank import get_all_questions
from tools.benchmark_learning_policy import compare_policy_benchmarks
from tools.generate_coverage_report import PROFILE_RULES, build_coverage_report, write_coverage_report
from tools.validate_question_bank import validate_question_bank


def _check(name: str, passed: bool, detail: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail or {}}


def evaluate_release_readiness(profile: str | None = None) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    readiness_profile = (profile or os.getenv("READINESS_PROFILE", "complete")).strip().lower()
    if readiness_profile == "production":
        readiness_profile = "complete"
    if readiness_profile not in PROFILE_RULES:
        raise ValueError(f"unknown readiness profile: {readiness_profile}")
    coverage_report = write_coverage_report()
    profile_status = coverage_report["profiles"][readiness_profile]
    bank_errors = validate_question_bank()
    questions = get_all_questions()
    chapters = {q.chapter_id for q in questions}
    contract_text = Path("docs/api/openapi_contract_v1.json").read_text(encoding="utf-8")
    schema_source = Path("backend/app/schemas.py").read_text(encoding="utf-8")
    question_dto_source = schema_source.split("class QuestionDTO", 1)[1].split("class AnswerRequest", 1)[0]
    frontend = Path("frontend/src/index.html").read_text(encoding="utf-8")
    offline_app = Path("frontend/src/offline-app.js").read_text(encoding="utf-8")
    learning_engine = Path("frontend/src/learning-engine.js").read_text(encoding="utf-8")
    client_source = offline_app + "\n" + learning_engine
    service_worker = Path("frontend/src/service-worker.js").read_text(encoding="utf-8")
    static_bank = json.loads(Path("frontend/src/question-bank.json").read_text(encoding="utf-8"))
    bank_version = current_bank_version()
    model_name = current_model_name()
    coverage_config = Path(".coveragerc").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    pages_workflow = Path(".github/workflows/static-pwa-pages.yml").read_text(encoding="utf-8")
    operations_source = Path("backend/app/api/operations.py").read_text(encoding="utf-8")
    benchmark = compare_policy_benchmarks(seed=11, rounds=120)
    adaptive = benchmark["results"][ADAPTIVE_POLICY]
    service_smoke = _run_service_smoke()
    question_count = len(questions)
    static_question_count = len(static_bank.get("questions", []))
    bootstrap_profile = readiness_profile == "bootstrap"

    checks.append(_check("question_bank_valid", not bank_errors, {"errors": bank_errors[:5]}))
    checks.append(
        _check(
            "readiness_profile_question_count",
            profile_status["passed"] or question_count >= profile_status["min_questions"],
            {
                "profile": readiness_profile,
                "questions": question_count,
                "min_questions": profile_status["min_questions"],
            },
        )
    )
    checks.append(
        _check(
            "syllabus_coverage",
            coverage_report["covered_chapters"] >= profile_status["min_chapters"]
            and (
                profile_status["min_per_chapter"] == 0
                or profile_status["chapters_meeting_floor"] == coverage_report["expected_chapters"]
            ),
            {"questions": question_count, "chapters": len(chapters), "bank_version": bank_version, "profile": readiness_profile},
        )
    )
    checks.append(
        _check(
            "coverage_report_quality",
            coverage_report["profiles"][readiness_profile]["passed"],
            {
                "profile": readiness_profile,
                "total_questions": coverage_report["total_questions"],
                "covered_chapters": coverage_report["covered_chapters"],
                "missing_chapters": coverage_report["missing_chapters"][:10],
                "active_review_warning_count": coverage_report["active_review_warning_count"],
                "legal_source_missing": coverage_report["legal_source_missing"],
                "duplicate_suspect_count": coverage_report["duplicate_suspect_count"],
                "correct_index_window_issues": coverage_report["correct_index_window_issues"][:3],
                "difficulty_balance_failures": profile_status["difficulty_balance_failures"][:10],
            },
        )
    )
    checks.append(
        _check(
            "answer_key_safety",
            "correct_index" not in question_dto_source and "explanation" not in question_dto_source,
            {"contract": "docs/api/openapi_contract_v1.json"},
        )
    )
    checks.append(
        _check(
            "pwa_and_recovery_ui",
            Path("frontend/src/manifest.webmanifest").exists()
            and Path("frontend/src/service-worker.js").exists()
            and Path("frontend/src/question-bank.json").exists()
            and Path("frontend/src/offline-app.js").exists()
            and Path("frontend/src/styles.css").exists()
            and "./learning-engine.js" in offline_app
            and "exportData" in offline_app and "exportData" in learning_engine
            and "importData" in offline_app and "importData" in learning_engine
            and "question-bank.json" in service_worker,
        )
    )
    checks.append(
        _check(
            "question_bank_cache_network_first",
            "QUESTION_BANK_URL" in service_worker
            and "url.pathname === questionBankPath" in service_worker
            and "questionBankNetworkFirst(request)" in service_worker
            and "cache: 'no-store'" in service_worker,
        )
    )
    checks.append(
        _check(
            "static_offline_pwa",
            static_bank.get("schema_version") == "gtest_quiz_static_bank_v1"
            and static_question_count == question_count
            and "http://localhost" not in frontend
            and "http://localhost" not in offline_app
            and "/api/v1" not in frontend
            and "/api/v1" not in offline_app
            and "localStorage" in learning_engine
            and "createEngine" in offline_app
            and "export function createEngine" in learning_engine
            and "buildSession" in offline_app and "buildSession" in learning_engine
            and "updateSchedule" in learning_engine,
            {"static_questions": static_question_count},
        )
    )
    checks.append(
        _check(
            "static_bank_build_metadata",
            bool(static_bank.get("meta", {}).get("content_hash"))
            and "generated_at" not in static_bank.get("meta", {})
            and "git_commit" not in static_bank.get("meta", {})
            and static_bank.get("meta", {}).get("question_count") == question_count,
            {"meta": static_bank.get("meta", {})},
        )
    )
    checks.append(
        _check(
            "gemini35_epoch",
            static_bank.get("meta", {}).get("bank_version") == bank_version
            and "bankVersion" in learning_engine
            and "gtestQuizOfflineStateV1" in learning_engine
            and "normalizeState" in learning_engine
            and "gemini-3.5-flash" in Path("gtest_quiz/bank_epoch.py").read_text(encoding="utf-8"),
            {"bank_version": bank_version, "model": model_name},
        )
    )
    checks.append(
        _check(
            "zero_cost_static_delivery",
            Path("frontend/src/.nojekyll").exists()
            and "actions/upload-pages-artifact" in pages_workflow
            and "actions/deploy-pages" in pages_workflow
            and "path: frontend/src" in pages_workflow,
        )
    )
    checks.append(
        _check(
            "session_auth_model",
            "/api/v1/auth/start" in contract_text
            and "/api/v1/auth/refresh" in contract_text
            and "/api/v1/auth/login" not in contract_text
            and "/api/v1/auth/register" not in contract_text
            and "password" not in frontend.lower()
            and "password" not in client_source.lower()
            and "LoginRequest" not in schema_source
            and "RegisterRequest" not in schema_source,
        )
    )
    checks.append(
        _check(
            "account_recovery_contract",
            "/api/v1/account/export" in contract_text
            and "/api/v1/account/import" in contract_text
            and "/api/v1/account/audit" in contract_text,
        )
    )
    checks.append(
        _check(
            "observability_contract",
            "/api/v1/operations/metrics" in contract_text
            and "user_from_authorization" in operations_source,
        )
    )
    checks.append(_check("service_smoke", service_smoke["passed"], service_smoke))
    checks.append(
        _check(
            "ci_coverage_scope_documented",
            "backend/app/api/*" in coverage_config
            and "gtest_quiz/ui.py" in coverage_config
            and "CI release gate" in readme
            and "release_readiness.py" in readme
            and "coverage" in readme.lower(),
            {"coverage_config": ".coveragerc"},
        )
    )
    checks.append(
        _check(
            "precision_benchmark",
            bool(adaptive.get("bootstrap_empty_bank"))
            or bootstrap_profile
            or (bootstrap_profile and bool(adaptive.get("limited_by_bank_size")))
            or (adaptive["scheduled_items"] >= 90 and adaptive["covered_chapters"] >= 20),
            {"adaptive": adaptive, "deltas": benchmark["deltas"]},
        )
    )
    checks.append(
        _check(
            "deployment_profiles",
            all((Path("deploy/profiles") / f"{name}.json").exists() for name in ["local", "classroom", "hosted"]),
        )
    )
    passed = all(check["passed"] for check in checks)
    return {"passed": passed, "profile": readiness_profile, "checks": checks, "benchmark": benchmark}


def _run_service_smoke() -> Dict[str, Any]:
    runtime_root = Path(".runtime/release_readiness").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=str(runtime_root), ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        service = QuizService(db_path=str(root / "quiz.db"), meta_path=str(root / "meta.json"))
        created = service.start_session()
        user = service.user_from_token(created["token"])
        refreshed = service.refresh_session(int(user["id"]))
        try:
            service.user_from_token(created["token"])
            old_token_revoked = False
        except UnauthorizedError:
            old_token_revoked = True
        user = service.user_from_token(refreshed["token"])
        selection = service.next_question(int(user["id"]))
        if selection is None:
            return {"passed": True, "bootstrap_empty_bank": True, "old_token_revoked": old_token_revoked}
        wrong_index = next(idx for idx in range(4) if idx != selection.question.correct_index)
        service.import_account(
            int(user["id"]),
            {
                "answers": [
                    {
                        "question_id": selection.question.id,
                        "chapter_id": "forged",
                        "selected_index": wrong_index,
                        "correct": 1,
                    }
                ],
                "learning_items": [],
            },
        )
        stats = service.storage.user_stats(int(user["id"]))
        return {
            "passed": old_token_revoked and stats["correct_answers"] == 0,
            "old_token_revoked": old_token_revoked,
            "import_recomputed_correctness": stats["correct_answers"] == 0,
        }


def main() -> None:
    result = evaluate_release_readiness()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
