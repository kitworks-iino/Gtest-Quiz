"""CLI entrypoint for the automatically selected Gemini question-bank pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gtest_quiz.bank_epoch import current_bank_version, current_model_name
from gtest_quiz.env import get_env, load_dotenv
from gtest_quiz.refill_pipeline import GeminiQuestionGenerator, RefillConfig, run_refill
from tools.generate_coverage_report import build_coverage_report, write_coverage_report


COMPLETE_TARGET_QUESTIONS_DEFAULT = 550
EXPANDED_TARGET_QUESTIONS_DEFAULT = 1000


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_last_refill_result() -> dict[str, Any]:
    path = ROOT / "bank/meta.json"
    if not path.exists():
        return {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    result = meta.get("last_refill_result") or meta.get("content_factory") or {}
    return result if isinstance(result, dict) else {}


def _adaptive_daily_target(default_target: int) -> int:
    last = _load_last_refill_result()
    accepted = int(last.get("accepted", 0) or 0)
    rate_limit_errors = int(last.get("rate_limit_errors", 0) or 0)
    errors = int(last.get("errors", 0) or 0)
    previous_target = int(last.get("target_accepts", last.get("target", default_target)) or default_target)
    if rate_limit_errors or (errors and accepted == 0):
        basis = accepted if accepted > 0 else previous_target
        return max(5, min(default_target, max(5, basis // 2)))
    if accepted >= previous_target and errors == 0:
        return min(50, previous_target + 5)
    if accepted > 0:
        return max(5, min(default_target, accepted + 5))
    return default_target


def _adaptive_seed_target(*, default_target: int, max_target: int, remaining: int) -> int:
    last = _load_last_refill_result()
    accepted = int(last.get("accepted", 0) or 0)
    rate_limit_errors = int(last.get("rate_limit_errors", 0) or 0)
    errors = int(last.get("errors", 0) or 0)
    previous_target = int(last.get("target_accepts", last.get("target", default_target)) or default_target)
    target = default_target
    if rate_limit_errors or (errors and accepted == 0):
        basis = accepted if accepted > 0 else previous_target
        target = max(5, min(default_target, max(5, basis // 2)))
    elif accepted >= previous_target and errors == 0:
        target = min(max_target, previous_target + 5)
    elif accepted > 0:
        target = min(max_target, accepted + 5)
    return max(0, min(target, remaining, max_target))


def _target_for_mode(mode: str, explicit_target: str | None) -> int:
    seed_default = _env_int("SEED_TARGET_DEFAULT", 15)
    seed_max = _env_int("SEED_TARGET_MAX", 30)
    complete_target = _env_int("COMPLETE_TARGET_QUESTIONS", COMPLETE_TARGET_QUESTIONS_DEFAULT)
    expanded_target = _env_int("EXPANDED_TARGET_QUESTIONS", EXPANDED_TARGET_QUESTIONS_DEFAULT)
    current_count = int(build_coverage_report().get("total_questions", 0))
    target_goal = expanded_target if mode == "build_to_expanded" else complete_target
    remaining = max(0, target_goal - current_count)
    if explicit_target:
        try:
            explicit = int(explicit_target)
        except ValueError as exc:
            raise SystemExit(f"invalid target: {explicit_target}") from exc
        if mode in {"seed", "reset_and_seed", "build_to_complete", "build_to_expanded"}:
            return max(0, min(explicit, seed_max, remaining if mode.startswith("build_to_") else explicit))
        return explicit
    if mode in {"build_to_complete", "build_to_expanded"}:
        return _adaptive_seed_target(default_target=seed_default, max_target=seed_max, remaining=remaining)
    if mode in {"seed", "reset_and_seed"}:
        return _adaptive_seed_target(default_target=seed_default, max_target=seed_max, remaining=_env_int("INITIAL_TARGET_PER_RUN", seed_default))
    default_daily = _env_int("DAILY_TARGET", 10)
    return _adaptive_daily_target(default_daily) if mode == "daily" else default_daily


def _stats_payload(stats: Any) -> dict[str, Any]:
    return {
        "model_name": stats.model_name,
        "model_selection": stats.model_selection,
        "bank_version": stats.bank_version,
        "mode": stats.mode,
        "target": getattr(stats, "target", 0),
        "reset_performed": stats.reset_performed,
        "old_active_question_count": stats.old_active_question_count,
        "deleted_question_count": stats.deleted_question_count,
        "active_question_count_before": stats.active_question_count_before,
        "generated": stats.generated,
        "accepted": stats.accepted,
        "rejected": stats.rejected,
        "duplicates": stats.duplicates,
        "errors": stats.errors,
        "queued_for_review": stats.queued_for_review,
        "api_call_count": stats.api_call_count,
        "rate_limit_errors": stats.rate_limit_errors,
        "correct_index_distribution_before": stats.correct_index_distribution_before,
        "correct_index_distribution_generated": stats.correct_index_distribution_generated,
        "correct_index_distribution_after": stats.correct_index_distribution_after,
        "active_question_count_after": stats.active_question_count_after,
        "new_active_question_count": stats.active_question_count_after,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the current Gemini question-bank generation pipeline.")
    parser.add_argument(
        "--mode",
        choices=["daily", "seed", "reset_and_seed", "replace", "build_to_complete", "build_to_expanded", "validate_only", "resolve_model_only"],
        default=os.getenv("GENERATION_MODE", "daily"),
    )
    parser.add_argument("--target", default=os.getenv("GENERATION_TARGET"))
    args = parser.parse_args()

    load_dotenv()
    model_name = current_model_name()
    bank_version = current_bank_version()
    if args.mode == "validate_only":
        report = write_coverage_report()
        print(json.dumps({"mode": args.mode, "bank_version": bank_version, "coverage": report}, ensure_ascii=False, indent=2))
        return
    if args.mode == "resolve_model_only":
        api_key = get_env("GEMINI_API_KEY")
        if not api_key:
            raise SystemExit("GEMINI_API_KEY is not set")
        generator = GeminiQuestionGenerator(api_key=api_key, model_name=model_name)
        print(json.dumps({"mode": args.mode, "model_selection": generator.model_selection,
                          "api_call_count": 0}, ensure_ascii=False, indent=2))
        return
    target = _target_for_mode(args.mode, args.target)
    print(
        f"refill_config model_name={model_name} bank_version={bank_version} mode={args.mode} target={target}",
        file=sys.stderr,
    )

    config = RefillConfig(
        model_name=model_name,
        target_daily=target,
        max_retry=_env_int("MAX_RETRY", 3),
        min_explanation_length=_env_int("MIN_EXPLANATION_LENGTH", 120),
        max_runtime_minutes=_env_int("MAX_RUNTIME_MINUTES", 25),
        mode=args.mode,
        bank_version=bank_version,
        per_chapter_floor=_env_int("MIN_PER_CHAPTER_COMPLETE", 10 if args.mode != "build_to_expanded" else 15),
        reset_question_bank=_env_bool("RESET_QUESTION_BANK") or args.mode == "reset_and_seed",
    )
    stats = run_refill(config)
    write_coverage_report()
    payload = _stats_payload(stats)
    payload["target"] = target
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
