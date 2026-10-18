"""Content refill pipeline backed by the schema-first content factory."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel

from gtest_quiz.bank_epoch import DEFAULT_GEMINI_MODEL, DEFAULT_QUESTION_BANK_EPOCH
from gtest_quiz.content_factory import (
    CoverageTarget,
    PROVENANCE_PATH,
    QUESTION_BANK_PATH,
    REVIEW_QUEUE_PATH,
    ContentFactory,
    FactoryConfig,
    FactoryStats,
    GeneratedQuestionSpec,
    load_existing_items,
)
from gtest_quiz.env import get_env, load_dotenv
from gtest_quiz.meta import MetaManager
from gtest_quiz.question_quality import validate_generated_question

NEXT_QUESTION_BANK_PATH = Path("bank/question_bank.next.jsonl")
NEXT_REVIEW_QUEUE_PATH = Path("bank/generated_review_queue.next.jsonl")
NEXT_PROVENANCE_PATH = Path("bank/question_provenance.next.jsonl")


class RefillConfig:
    def __init__(
        self,
        *,
        model_name: str = DEFAULT_GEMINI_MODEL,
        target_daily: int = 50,
        max_retry: int = 3,
        min_explanation_length: int = 120,
        sleep_seconds_on_429: float = 2.0,
        hard_stop_on_near_quota: bool = True,
        mode: str = "daily",
        bank_version: str = DEFAULT_QUESTION_BANK_EPOCH,
        max_runtime_minutes: int = 25,
        reset_question_bank: bool = False,
        per_chapter_floor: int = 10,
    ) -> None:
        self.model_name = model_name
        self.target_daily = target_daily
        self.max_retry = max_retry
        self.min_explanation_length = min_explanation_length
        self.sleep_seconds_on_429 = sleep_seconds_on_429
        self.hard_stop_on_near_quota = hard_stop_on_near_quota
        self.mode = mode
        self.bank_version = bank_version
        self.max_runtime_minutes = max_runtime_minutes
        self.reset_question_bank = reset_question_bank
        self.per_chapter_floor = per_chapter_floor


class RefillStats:
    def __init__(self) -> None:
        self.generated = 0
        self.accepted = 0
        self.rejected = 0
        self.duplicates = 0
        self.errors = 0
        self.queued_for_review = 0
        self.api_call_count = 0
        self.rate_limit_errors = 0
        self.mode = ""
        self.model_name = ""
        self.bank_version = ""
        self.reset_performed = False
        self.old_active_question_count = 0
        self.deleted_question_count = 0
        self.active_question_count_before = 0
        self.active_question_count_after = 0
        self.correct_index_distribution_before = {str(index): 0 for index in range(4)}
        self.correct_index_distribution_generated = {str(index): 0 for index in range(4)}
        self.correct_index_distribution_after = {str(index): 0 for index in range(4)}

    @classmethod
    def from_factory(cls, stats: FactoryStats, *, config: RefillConfig) -> "RefillStats":
        item = cls()
        item.generated = stats.generated
        item.accepted = stats.accepted
        item.rejected = stats.rejected
        item.duplicates = stats.duplicates
        item.errors = stats.errors
        item.queued_for_review = stats.queued_for_review
        item.api_call_count = stats.api_call_count
        item.rate_limit_errors = stats.rate_limit_errors
        item.correct_index_distribution_generated = dict(stats.correct_index_distribution_generated)
        item.mode = config.mode
        item.model_name = config.model_name
        item.bank_version = config.bank_version
        return item


def _safe_parse_json(text: str) -> Optional[Dict[str, object]]:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                return None
        return None


def _load_generator(api_key: str) -> Any:
    from google import genai

    return genai.Client(api_key=api_key)


class GeminiQuestionGenerator:
    def __init__(self, *, api_key: str, model_name: str, max_retry: int = 3, sleep_seconds: float = 0.4) -> None:
        self.model_name = model_name
        self.max_retry = max_retry
        self.sleep_seconds = sleep_seconds
        self.api_call_count = 0
        self.rate_limit_errors = 0
        self.generator = _load_generator(api_key)

    def generate(self, prompt: str, schema: type[GeneratedQuestionSpec]) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retry):
            try:
                data = self._generate_once(prompt, schema)
                if isinstance(data, dict):
                    return data
            except Exception as e:
                last_error = e
                if _is_rate_limit_error(e):
                    self.rate_limit_errors += 1
                elif getattr(e, "code", None) in {400, 401, 403, 404}:
                    raise
            if attempt + 1 < self.max_retry:
                time.sleep(self.sleep_seconds)
        if last_error:
            raise last_error
        raise RuntimeError("Gemini did not return a structured question")

    def _generate_once(self, prompt: str, schema: type[BaseModel]) -> Optional[Dict[str, Any]]:
        self.api_call_count += 1
        response = self.generator.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": schema,
                "temperature": 0.62,
            },
        )
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, BaseModel):
            return parsed.model_dump()
        if isinstance(parsed, dict):
            return parsed
        return _safe_parse_json(getattr(response, "text", "") or "")


def _is_rate_limit_error(error: Exception) -> bool:
    text = str(error).lower()
    return "429" in text or "rate limit" in text or "quota" in text or "resource exhausted" in text


def _write_jsonl(path: Path, rows: list[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _correct_index_distribution(rows: list[Dict[str, Any]]) -> Dict[str, int]:
    counts = {str(index): 0 for index in range(4)}
    for row in rows:
        value = row.get("correct_index")
        if isinstance(value, int) and str(value) in counts:
            counts[str(value)] += 1
    return counts


def _prepare_next_bank(*, copy_active: bool = False) -> None:
    for active, staged in (
        (QUESTION_BANK_PATH, NEXT_QUESTION_BANK_PATH),
        (REVIEW_QUEUE_PATH, NEXT_REVIEW_QUEUE_PATH),
        (PROVENANCE_PATH, NEXT_PROVENANCE_PATH),
    ):
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(active.read_bytes() if copy_active and active.exists() else b"")



def _replacement_candidates(config: RefillConfig) -> list[Dict[str, Any]]:
    rows = load_existing_items(QUESTION_BANK_PATH)
    candidates: list[Dict[str, Any]] = []
    for row in rows:
        provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
        if row.get("bank_version") != config.bank_version or provenance.get("model") != config.model_name:
            candidates.append(row)
            continue
        quality = validate_generated_question(row, min_explanation_length=config.min_explanation_length)
        if not quality.is_valid:
            candidates.append(row)
    return candidates


def _replacement_targets(config: RefillConfig, candidates: list[Dict[str, Any]]) -> list[CoverageTarget]:
    targets: list[CoverageTarget] = []
    for row in candidates[: config.target_daily]:
        provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
        concepts_raw = provenance.get("concepts") or row.get("concepts") or []
        concepts = tuple(str(concept) for concept in concepts_raw if str(concept).strip())
        targets.append(
            CoverageTarget(
                chapter_group=str(row.get("chapter_group", "")),
                chapter_id=str(row.get("chapter_id", "")),
                current_count=0,
                target_count=1,
                priority=1.0,
                concepts=concepts,
            )
        )
    return targets


def _drop_replaced_items(
    candidates: list[Dict[str, Any]], new_rows: list[Dict[str, Any]],
    *, bank_path: Path = NEXT_QUESTION_BANK_PATH, provenance_path: Path = NEXT_PROVENANCE_PATH,
) -> int:
    """Replace only old questions in chapters that actually gained a candidate."""
    remaining = list(candidates)
    drop_ids: set[str] = set()
    for new_row in new_rows:
        for candidate in remaining:
            if all(candidate.get(key) == new_row.get(key) for key in ("chapter_group", "chapter_id")):
                drop_ids.add(str(candidate["id"]))
                remaining.remove(candidate)
                break
    if drop_ids:
        _write_jsonl(bank_path, [row for row in load_existing_items(bank_path) if str(row.get("id")) not in drop_ids])
        _write_jsonl(provenance_path, [row for row in load_existing_items(provenance_path) if str(row.get("id")) not in drop_ids])
    return len(drop_ids)


def _swap_next_bank(*, config: RefillConfig, meta_path: str, stats: RefillStats, before_count: int, deleted_count: int | None = None) -> None:
    from tools.validate_question_bank import validate_question_bank

    errors = validate_question_bank(NEXT_QUESTION_BANK_PATH)
    if errors:
        raise RuntimeError("next question bank validation failed: " + "; ".join(errors[:5]))
    for source, target in (
        (NEXT_QUESTION_BANK_PATH, QUESTION_BANK_PATH),
        (NEXT_REVIEW_QUEUE_PATH, REVIEW_QUEUE_PATH),
        (NEXT_PROVENANCE_PATH, PROVENANCE_PATH),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)

    total_questions = len(load_existing_items(QUESTION_BANK_PATH))
    meta = MetaManager(meta_path)
    meta.load()
    meta.meta["bank_version"] = config.bank_version
    meta.meta["question_bank"] = {
        "path": str(QUESTION_BANK_PATH),
        "bank_version": config.bank_version,
        "total_questions": total_questions,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    meta.meta["content_factory"] = {
        "last_refill_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": config.model_name,
        "bank_version": config.bank_version,
        "mode": config.mode,
        "target_accepts": config.target_daily,
        "generated": stats.generated,
        "accepted": stats.accepted,
        "queued_for_review": stats.queued_for_review,
        "rejected": stats.rejected,
        "duplicates": stats.duplicates,
        "errors": stats.errors,
        "api_call_count": stats.api_call_count,
        "rate_limit_errors": stats.rate_limit_errors,
        "correct_index_distribution_before": stats.correct_index_distribution_before,
        "correct_index_distribution_generated": stats.correct_index_distribution_generated,
        "correct_index_distribution_after": _correct_index_distribution(load_existing_items(QUESTION_BANK_PATH)),
        "reset_performed": config.reset_question_bank or config.mode == "reset_and_seed",
        "old_active_question_count": before_count,
        "deleted_question_count": before_count if deleted_count is None else deleted_count,
        "new_active_question_count": total_questions,
    }
    meta.meta["last_chapter_id"] = None
    rows = load_existing_items(QUESTION_BANK_PATH)
    if rows:
        meta.meta["last_chapter_id"] = str(rows[-1].get("chapter_id", ""))
    meta.save()


def _save_last_refill_result(*, config: RefillConfig, meta_path: str, stats: RefillStats) -> None:
    meta = MetaManager(meta_path)
    meta.load()
    meta.meta["last_refill_result"] = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": config.model_name,
        "bank_version": config.bank_version,
        "mode": config.mode,
        "target_accepts": config.target_daily,
        "generated": stats.generated,
        "accepted": stats.accepted,
        "queued_for_review": stats.queued_for_review,
        "rejected": stats.rejected,
        "duplicates": stats.duplicates,
        "errors": stats.errors,
        "api_call_count": stats.api_call_count,
        "rate_limit_errors": stats.rate_limit_errors,
        "correct_index_distribution_before": stats.correct_index_distribution_before,
        "correct_index_distribution_generated": stats.correct_index_distribution_generated,
        "correct_index_distribution_after": stats.correct_index_distribution_after,
    }
    meta.save()


def run_refill(config: RefillConfig, meta_path: str = "bank/meta.json") -> RefillStats:
    load_dotenv()
    api_key = get_env("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    before_rows = load_existing_items(QUESTION_BANK_PATH)
    before_count = len(before_rows)
    before_distribution = _correct_index_distribution(before_rows)
    reset_info = {"old_active_question_count": before_count, "deleted_question_count": 0, "new_active_question_count": before_count}
    reset_requested = config.reset_question_bank or config.mode == "reset_and_seed"

    meta = MetaManager(meta_path)
    meta.load()
    quota = meta.quota
    if config.hard_stop_on_near_quota and quota.is_near_limit(0.9):
        stats = RefillStats()
        stats.mode = config.mode
        stats.model_name = config.model_name
        stats.bank_version = config.bank_version
        stats.active_question_count_before = before_count
        stats.active_question_count_after = before_count
        stats.correct_index_distribution_before = before_distribution
        stats.correct_index_distribution_after = before_distribution
        return stats

    generator = GeminiQuestionGenerator(
        api_key=api_key,
        model_name=config.model_name,
        max_retry=config.max_retry,
        sleep_seconds=config.sleep_seconds_on_429,
    )
    allow_growth = config.mode in {"daily", "replace", "build_to_complete", "build_to_expanded"}
    question_bank_path = QUESTION_BANK_PATH
    review_queue_path = REVIEW_QUEUE_PATH
    provenance_path = PROVENANCE_PATH
    update_meta = True
    explicit_targets = None
    replacement_candidates: list[Dict[str, Any]] = []
    if reset_requested or config.mode == "replace":
        _prepare_next_bank(copy_active=not reset_requested)
        question_bank_path = NEXT_QUESTION_BANK_PATH
        review_queue_path = NEXT_REVIEW_QUEUE_PATH
        provenance_path = NEXT_PROVENANCE_PATH
        update_meta = False
        allow_growth = True
        if not reset_requested:
            replacement_candidates = _replacement_candidates(config)
            explicit_targets = _replacement_targets(config, replacement_candidates)

    factory = ContentFactory(
        FactoryConfig(
            model_name=config.model_name,
            target_accepts=config.target_daily,
            min_explanation_length=config.min_explanation_length,
            max_attempts_per_target=config.max_retry,
            allow_over_floor_growth=allow_growth,
            bank_version=config.bank_version,
            mode=config.mode,
            max_runtime_minutes=config.max_runtime_minutes,
            per_chapter_floor=config.per_chapter_floor,
            update_meta=update_meta,
            explicit_targets=explicit_targets,
            question_bank_path=question_bank_path,
            review_queue_path=review_queue_path,
            provenance_path=provenance_path,
        ),
        generator,
    )
    stats = RefillStats.from_factory(factory.run(meta_path=meta_path), config=config)
    if reset_requested:
        if stats.accepted > 0:
            _swap_next_bank(config=config, meta_path=meta_path, stats=stats, before_count=before_count)
            reset_info = {
                "old_active_question_count": before_count,
                "deleted_question_count": before_count,
                "new_active_question_count": len(load_existing_items(QUESTION_BANK_PATH)),
            }
        else:
            reset_info = {
                "old_active_question_count": before_count,
                "deleted_question_count": 0,
                "new_active_question_count": before_count,
            }
    elif config.mode == "replace" and stats.accepted:
        new_rows = load_existing_items(NEXT_QUESTION_BANK_PATH)[before_count:]
        removed = _drop_replaced_items(replacement_candidates, new_rows)
        _swap_next_bank(config=config, meta_path=meta_path, stats=stats, before_count=before_count, deleted_count=removed)
        reset_info["deleted_question_count"] = removed
    stats.api_call_count = generator.api_call_count
    stats.rate_limit_errors = generator.rate_limit_errors
    stats.reset_performed = bool(reset_requested and stats.accepted > 0)
    stats.old_active_question_count = reset_info["old_active_question_count"]
    stats.deleted_question_count = reset_info["deleted_question_count"]
    stats.active_question_count_before = before_count
    after_rows = load_existing_items(QUESTION_BANK_PATH)
    stats.active_question_count_after = len(after_rows)
    stats.correct_index_distribution_before = before_distribution
    stats.correct_index_distribution_after = _correct_index_distribution(after_rows)
    _save_last_refill_result(config=config, meta_path=meta_path, stats=stats)
    return stats
