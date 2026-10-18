from __future__ import annotations

import json
import time
import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol

from pydantic import BaseModel, Field, ValidationError

from gtest_quiz.bank_epoch import DEFAULT_GEMINI_MODEL, DEFAULT_QUESTION_BANK_EPOCH
from gtest_quiz.meta import MetaManager
from gtest_quiz.question_quality import (
    ValidationResult,
    add_question_to_index,
    correct_index_distribution_errors,
    has_positional_choice_reference,
    build_duplicate_index,
    is_probable_duplicate,
    validate_generated_question,
)


PROMPT_VERSION = "gtest_factory_gemini35_v2_2026-10-18"
QUESTION_BANK_PATH = Path("bank/question_bank.jsonl")
REVIEW_QUEUE_PATH = Path("bank/generated_review_queue.jsonl")
PROVENANCE_PATH = Path("bank/question_provenance.jsonl")
LEGAL_CHAPTER_GROUPS = {"AIに関する法律と契約", "AI倫理・AIガバナンス"}
LEGAL_TERMS = ("法律", "契約", "個人情報", "著作権", "知的財産", "ガイドライン", "倫理", "ガバナンス")
LEGAL_SOURCE_FIELDS = (
    "source_url",
    "source_title",
    "source_version",
    "source_checked_at",
    "legal_basis",
)
LEGAL_EXTENDED_SOURCE_FIELDS = (
    "source_quote_short",
    "source_section",
    "source_organization",
)


class GeneratedQuestionSpec(BaseModel):
    question: str = Field(min_length=18)
    choices: List[str] = Field(min_length=4, max_length=4)
    correct_index: int = Field(ge=0, le=3)
    explanation: str = Field(min_length=120)
    difficulty: str = Field(pattern="^(basic|standard|advanced)$")
    syllabus_node: str = Field(min_length=1)
    concepts: List[str] = Field(default_factory=list)
    source_hint: str = ""
    source_url: str = ""
    source_title: str = ""
    source_version: str = ""
    source_checked_at: str = ""
    legal_basis: str = ""
    source_quote_short: str = ""
    source_section: str = ""
    source_organization: str = ""


class QuestionGenerator(Protocol):
    def generate(self, prompt: str, schema: type[GeneratedQuestionSpec]) -> Dict[str, Any]:
        ...


@dataclass(frozen=True)
class FactoryConfig:
    model_name: str = DEFAULT_GEMINI_MODEL
    prompt_version: str = PROMPT_VERSION
    target_accepts: int = 80
    min_explanation_length: int = 120
    review_threshold: int = 95
    max_attempts_per_target: int = 3
    per_chapter_floor: int = 10
    allow_over_floor_growth: bool = True
    bank_version: str = DEFAULT_QUESTION_BANK_EPOCH
    mode: str = "daily"
    max_runtime_minutes: int = 25
    update_meta: bool = True
    explicit_targets: Optional[List["CoverageTarget"]] = None
    question_bank_path: Path = QUESTION_BANK_PATH
    review_queue_path: Path = REVIEW_QUEUE_PATH
    provenance_path: Path = PROVENANCE_PATH


@dataclass
class FactoryStats:
    generated: int = 0
    accepted: int = 0
    queued_for_review: int = 0
    rejected: int = 0
    duplicates: int = 0
    errors: int = 0
    targets: int = 0
    api_call_count: int = 0
    rate_limit_errors: int = 0
    correct_index_distribution_generated: Dict[str, int] = field(
        default_factory=lambda: {str(index): 0 for index in range(4)}
    )


@dataclass(frozen=True)
class CoverageTarget:
    chapter_group: str
    chapter_id: str
    current_count: int
    target_count: int
    priority: float
    rotation_score: int = 0
    concepts: tuple[str, ...] = ()
    desired_difficulty: str = "standard"


@dataclass(frozen=True)
class CandidateDecision:
    action: str
    validation: ValidationResult
    duplicate: bool
    reasons: List[str] = field(default_factory=list)
    review_score: int = 0
    review_reasons: List[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jsonl_append(path: Path, item: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def domain_for_chapter_group(chapter_group: str) -> str:
    return "法律分野" if chapter_group in LEGAL_CHAPTER_GROUPS else "技術分野"


def is_legal_target(chapter_group: str, chapter_id: str = "", domain: str = "") -> bool:
    text = f"{domain} {chapter_group} {chapter_id}"
    return domain == "法律分野" or any(term in text for term in LEGAL_TERMS)


def build_generation_prompt(target: CoverageTarget, prompt_version: str = PROMPT_VERSION) -> str:
    concept_hint = ", ".join(target.concepts) if target.concepts else "chapter core concepts"
    prompt = f"""
あなたはJDLA G検定の専門作問者です。
prompt_version: {prompt_version}

目的:
- 受験者の理解度を測る高品質な4択問題を1問作る
- 暗記だけでなく概念の違い、実務上の判断、代表的な落とし穴を問う
- 誤答はもっともらしいが、解説で明確に区別できるものにする
- 単純な用語暗記ではなく、概念理解・比較・適用判断を問う

出力制約:
- response_schema に完全準拠
- 正解は1つ
- explanation は120文字以上
- 選択肢は保存時に並べ替えるため、問題文・選択肢・解説で選択肢の番号・記号・位置を参照しない
- 解説では選択肢の具体的な内容・概念名を用いて正解理由と不正解理由を説明する
- explanation は「正解理由」と「不正解理由」または誤答との差分を含める
- syllabus_node には対象項目名を入れる
- 法律・倫理・ガイドライン系の問題では source_url/source_title/source_version/source_checked_at/legal_basis に公式出典を入れる

対象大分類: {target.chapter_group}
対象項目: {target.chapter_id}
対象概念: {concept_hint}
現在の問題数: {target.current_count}
目標問題数: {target.target_count}
""".strip()
    return prompt + (
        f"\nTarget difficulty: {target.desired_difficulty}"
        f"\nAdditional strict requirements:"
        f"\n- difficulty must be exactly \"{target.desired_difficulty}\"."
        "\n- For legal/guideline questions, use official sources and fill source_url, source_title, "
        "source_version, source_checked_at, legal_basis, source_quote_short, source_section, and source_organization. "
        "Keep source_quote_short brief."
    )


def load_existing_items(path: Path = QUESTION_BANK_PATH) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from error
            if not isinstance(data, dict):
                raise ValueError(f"{path}:{line_number}: expected a question object")
            items.append(data)
    return items


def _daily_rotation_score(chapter_id: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest = hashlib.sha256(f"{today}:{chapter_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def desired_difficulty_counts(target_count: int) -> Dict[str, int]:
    if target_count >= 15:
        basic = max(3, round(target_count * 0.25))
        advanced = max(3, round(target_count * 0.25))
    else:
        basic = max(2, round(target_count * 0.25))
        advanced = max(2, round(target_count * 0.25))
    standard = max(1, target_count - basic - advanced)
    return {"basic": basic, "standard": standard, "advanced": advanced}


def desired_difficulty_for_chapter(difficulty_counts: Dict[str, int], target_count: int) -> str:
    desired = desired_difficulty_counts(target_count)
    deficits = {
        difficulty: desired[difficulty] - int(difficulty_counts.get(difficulty, 0))
        for difficulty in ("basic", "standard", "advanced")
    }
    return max(("basic", "standard", "advanced"), key=lambda difficulty: (deficits[difficulty], desired[difficulty], difficulty))


def build_coverage_targets(
    meta: MetaManager,
    existing_items: Iterable[Dict[str, Any]],
    per_chapter_floor: int = 10,
    allow_over_floor_growth: bool = True,
) -> List[CoverageTarget]:
    counts: Dict[str, int] = {}
    groups: Dict[str, str] = {}
    difficulty_counts: Dict[str, Dict[str, int]] = {}
    for item in existing_items:
        chapter_id = str(item.get("chapter_id", ""))
        if not chapter_id:
            continue
        counts[chapter_id] = counts.get(chapter_id, 0) + 1
        groups.setdefault(chapter_id, str(item.get("chapter_group", "")))
        difficulty = str(item.get("difficulty", "standard"))
        difficulty_counts.setdefault(chapter_id, {"basic": 0, "standard": 0, "advanced": 0})
        if difficulty in difficulty_counts[chapter_id]:
            difficulty_counts[chapter_id][difficulty] += 1

    targets: List[CoverageTarget] = []
    chapters = meta.meta.get("chapters", {})
    for _group_key, group_val in chapters.items():
        group_label = str(group_val.get("label", ""))
        subchapters = group_val.get("subchapters", {})
        if not isinstance(subchapters, dict):
            continue
        for _sub_key, sub_val in subchapters.items():
            chapter_id = str(sub_val.get("label", ""))
            if not chapter_id:
                continue
            current = counts.get(chapter_id, 0)
            target_count = max(per_chapter_floor, int(float(sub_val.get("weight", 1.0)) * per_chapter_floor))
            deficit = max(0, target_count - current)
            rotation_score = _daily_rotation_score(chapter_id)
            if deficit > 0:
                priority = deficit / max(1, target_count)
            elif allow_over_floor_growth:
                surplus = current - target_count
                priority = float(sub_val.get("weight", 1.0)) / max(1, surplus + 1)
                target_count = current + 1
            else:
                priority = 0.0
            targets.append(
                CoverageTarget(
                    chapter_group=groups.get(chapter_id, group_label),
                    chapter_id=chapter_id,
                    current_count=current,
                    target_count=target_count,
                    priority=priority,
                    rotation_score=rotation_score,
                    desired_difficulty=desired_difficulty_for_chapter(difficulty_counts.get(chapter_id, {}), target_count),
                )
            )

    for chapter_id, current in counts.items():
        if not any(target.chapter_id == chapter_id for target in targets):
            targets.append(
                CoverageTarget(
                    chapter_group=groups.get(chapter_id, ""),
                    chapter_id=chapter_id,
                    current_count=current,
                    target_count=max(per_chapter_floor, current),
                    priority=0.0,
                    rotation_score=_daily_rotation_score(chapter_id),
                    desired_difficulty=desired_difficulty_for_chapter(difficulty_counts.get(chapter_id, {}), max(per_chapter_floor, current)),
                )
            )

    return sorted(targets, key=lambda t: (-t.priority, t.current_count, t.rotation_score, t.chapter_id))


def decide_candidate(
    data: Dict[str, Any],
    *,
    duplicate_index: Any,
    min_explanation_length: int,
    review_threshold: int,
    target: CoverageTarget | None = None,
) -> CandidateDecision:
    validation = validate_generated_question(data, min_explanation_length=min_explanation_length)
    review_score, review_reasons = review_generated_candidate(data, target=target, min_explanation_length=min_explanation_length)
    duplicate = is_probable_duplicate(str(data.get("question", "")), duplicate_index)
    reasons = [*validation.reasons, *review_reasons]
    if duplicate:
        reasons.append("probable duplicate")
    if duplicate:
        return CandidateDecision("duplicate", validation, duplicate, reasons, review_score, review_reasons)
    if validation.is_valid and review_score >= review_threshold and not review_reasons:
        return CandidateDecision("accept", validation, duplicate, reasons, review_score, review_reasons)
    if validation.score >= review_threshold or review_score >= review_threshold:
        return CandidateDecision("review", validation, duplicate, reasons, review_score, review_reasons)
    return CandidateDecision("reject", validation, duplicate, reasons, review_score, review_reasons)


def review_generated_candidate(
    data: Dict[str, Any],
    *,
    target: CoverageTarget | None = None,
    min_explanation_length: int = 120,
) -> tuple[int, List[str]]:
    score = 100
    reasons: List[str] = []
    choices = [str(choice).strip() for choice in data.get("choices", [])]
    correct_index = data.get("correct_index", -1)
    explanation = str(data.get("explanation", ""))
    concepts = [str(concept).strip() for concept in data.get("concepts", []) if str(concept).strip()]
    syllabus_node = str(data.get("syllabus_node", "")).strip()
    legal_scope = bool(target and is_legal_target(target.chapter_group, target.chapter_id)) or is_legal_target(
        str(data.get("chapter_group", "")),
        str(data.get("chapter_id", "")),
        str(data.get("domain", "")),
    )

    if len(choices) != 4 or len(set(choices)) != 4:
        score -= 30
        reasons.append("choices are not four unique options")
    if correct_index not in {0, 1, 2, 3}:
        score -= 40
        reasons.append("correct_index is not a single valid answer")
    if len(explanation) < min_explanation_length:
        score -= 20
        reasons.append("explanation is below production length")
    if not any(token in explanation for token in ("正解", "正しい", "理由", "なぜ", "ため")):
        score -= 10
        reasons.append("explanation does not clearly state why the answer is correct")
    if not any(token in explanation for token in ("不正解", "誤答", "他の選択肢", "一方", "ではない", "ではなく", "ありません", "差")):
        score -= 10
        reasons.append("explanation does not distinguish distractors")
    if any(has_positional_choice_reference(text) for text in [str(data.get("question", "")), explanation, *choices]):
        score -= 100
        reasons.append("choice position references are unsafe to shuffle; use option text or concept names")
    if not concepts:
        score -= 10
        reasons.append("concept tags are missing")
    if not syllabus_node:
        score -= 10
        reasons.append("syllabus_node is missing")
    if len(str(data.get("question", ""))) < 18:
        score -= 20
        reasons.append("question is too short for concept checking")
    if target and str(data.get("difficulty", "")).strip() != target.desired_difficulty:
        score -= 20
        reasons.append(f"difficulty does not match target: {target.desired_difficulty}")
    if legal_scope:
        missing_source = [field for field in LEGAL_SOURCE_FIELDS if not str(data.get(field, "")).strip()]
        if missing_source:
            score -= 100
            reasons.append("legal or guideline question is missing official source metadata: " + ", ".join(missing_source))

    return max(0, score), reasons


def shuffle_choices_for_record(
    choices: List[str],
    correct_index: int,
    *,
    seed_material: str,
    desired_correct_index: int | None = None,
) -> tuple[List[str], int, str]:
    seed = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    correct_choice = choices[correct_index]
    distractors = [choice for index, choice in enumerate(choices) if index != correct_index]
    random.Random(seed).shuffle(distractors)
    if desired_correct_index is None:
        desired_correct_index = int(seed[:8], 16) % 4
    desired_correct_index = max(0, min(3, desired_correct_index))
    shuffled = list(distractors)
    shuffled.insert(desired_correct_index, correct_choice)
    return shuffled, desired_correct_index, seed[:16]


def to_question_record(
    data: Dict[str, Any],
    *,
    target: CoverageTarget,
    config: FactoryConfig,
    decision: CandidateDecision,
    desired_correct_index: int | None = None,
) -> Dict[str, Any]:
    if decision.action != "accept":
        raise ValueError("Only accepted candidates may become question records")
    stamp = int(time.time() * 1000)
    original_choices = [str(choice) for choice in data.get("choices", [])]
    original_correct_index = int(data.get("correct_index", 0))
    shuffled_choices, shuffled_correct_index, shuffle_seed = shuffle_choices_for_record(
        original_choices,
        original_correct_index,
        seed_material=f"{config.bank_version}:{config.prompt_version}:{target.chapter_id}:{data.get('question', '')}:{stamp}",
        desired_correct_index=desired_correct_index,
    )
    record = {
        "id": f"AUTO_{stamp}_{hashlib.sha256(str(data.get('question', '')).encode('utf-8')).hexdigest()[:16]}",
        "source": "gemini35_content_factory",
        "created_at": _now_iso(),
        "bank_version": config.bank_version,
        "domain": domain_for_chapter_group(target.chapter_group),
        "chapter_group": target.chapter_group,
        "chapter_id": target.chapter_id,
        "difficulty": str(data.get("difficulty", "standard")),
        "question": str(data.get("question", "")),
        "choices": shuffled_choices,
        "correct_index": shuffled_correct_index,
        "explanation": str(data.get("explanation", "")),
        "syllabus": "G2024_v1.3",
        "provenance": {
            "model": config.model_name,
            "prompt_version": config.prompt_version,
            "bank_version": config.bank_version,
            "validator_score": decision.validation.score,
            "validator_reasons": decision.validation.reasons,
            "review_score": decision.review_score,
            "review_reasons": decision.review_reasons,
            "accepted_with_review_warnings": False,
            "choice_shuffle_seed": shuffle_seed,
            "desired_difficulty": target.desired_difficulty,
            "syllabus_node": str(data.get("syllabus_node", target.chapter_id)),
            "concepts": list(data.get("concepts", [])),
            "generated_at": _now_iso(),
        },
    }
    if is_legal_target(record["chapter_group"], record["chapter_id"], record["domain"]):
        for field in (*LEGAL_SOURCE_FIELDS, *LEGAL_EXTENDED_SOURCE_FIELDS):
            record[field] = str(data.get(field, "")).strip()
    return record


class BalancedAnswerPositions:
    """Fill the least-used answer slot in the current publication window."""
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.total = [0] * 4
        self.window = [0] * 4
        self.count = len(rows)
        for index, row in enumerate(rows):
            value = row.get("correct_index")
            if type(value) is int and 0 <= value < 4:
                self.total[value] += 1
                if index >= len(rows) - len(rows) % 100:
                    self.window[value] += 1

    def take(self) -> int:
        position = min(range(4), key=lambda index: (self.window[index], self.total[index], index))
        self.total[position] += 1
        self.window[position] += 1
        self.count += 1
        if self.count % 100 == 0:
            self.window = [0] * 4
        return position


class ContentFactory:
    def __init__(self, config: FactoryConfig, generator: QuestionGenerator) -> None:
        self.config = config
        self.generator = generator

    def run(self, meta_path: str = "bank/meta.json") -> FactoryStats:
        meta = MetaManager(meta_path)
        meta.load()
        existing = load_existing_items(self.config.question_bank_path)
        duplicate_index = build_duplicate_index(existing)
        targets = list(self.config.explicit_targets) if self.config.explicit_targets is not None else []
        if self.config.explicit_targets is None:
            targets = [
                target
                for target in build_coverage_targets(
                    meta,
                    existing,
                    per_chapter_floor=self.config.per_chapter_floor,
                    allow_over_floor_growth=self.config.allow_over_floor_growth,
                )
                if target.priority > 0
            ]

        stats = FactoryStats(targets=len(targets))
        accepted_records: List[Dict[str, Any]] = []
        accepted_candidates: list[tuple[CoverageTarget, Dict[str, Any], CandidateDecision]] = []
        positions = BalancedAnswerPositions(existing)
        deadline = time.monotonic() + (self.config.max_runtime_minutes * 60)
        attempt_budget = max(
            self.config.target_accepts * self.config.max_attempts_per_target * 4,
            len(targets) * self.config.max_attempts_per_target,
        )

        target_index = 0
        while targets and attempt_budget > 0 and stats.accepted < self.config.target_accepts and time.monotonic() < deadline:
            target = targets[target_index % len(targets)]
            target_index += 1
            if time.monotonic() >= deadline:
                break
            if stats.accepted >= self.config.target_accepts:
                break
            for _attempt in range(self.config.max_attempts_per_target):
                attempt_budget -= 1
                if time.monotonic() >= deadline:
                    break
                if attempt_budget < 0:
                    break
                if stats.accepted >= self.config.target_accepts:
                    break
                prompt = build_generation_prompt(target, self.config.prompt_version)
                try:
                    data = self.generator.generate(prompt, GeneratedQuestionSpec)
                    stats.generated += 1
                except Exception as e:
                    stats.errors += 1
                    if _is_transient_generation_error(str(e)):
                        # Generator retries are exhausted; do not multiply the
                        # same quota failure by the factory attempt budget.
                        attempt_budget = 0
                        break
                    if getattr(e, "code", None) in {400, 401, 403, 404}:
                        raise
                    self._queue_error(target, str(e))
                    continue

                try:
                    data = GeneratedQuestionSpec.model_validate(data, strict=True).model_dump()
                except ValidationError as error:
                    reasons = [f"schema: {item['loc']}: {item['msg']}" for item in error.errors()]
                    decision = CandidateDecision("reject", ValidationResult(False, 0, reasons), False, reasons)
                    stats.rejected += 1
                    self._queue_review(target, data, decision)
                    continue

                decision = decide_candidate(
                    data,
                    duplicate_index=duplicate_index,
                    min_explanation_length=self.config.min_explanation_length,
                    review_threshold=self.config.review_threshold,
                    target=target,
                )
                if decision.action == "duplicate":
                    stats.duplicates += 1
                    continue
                if decision.action == "reject":
                    stats.rejected += 1
                    self._queue_review(target, data, decision)
                    continue
                if decision.action == "review":
                    stats.queued_for_review += 1
                    self._queue_review(target, data, decision)
                    continue

                record = to_question_record(
                    data, target=target, config=self.config, decision=decision,
                    desired_correct_index=positions.take(),
                )
                accepted_records.append(record)
                accepted_candidates.append((target, data, decision))
                stats.accepted += 1
                key = str(record.get("correct_index", ""))
                if key in stats.correct_index_distribution_generated:
                    stats.correct_index_distribution_generated[key] += 1
                add_question_to_index(duplicate_index, str(record["question"]))
                break

        # Publish only a complete valid batch. A quota interruption must not
        # append a partial batch that would fail the same downstream gate.
        distribution_errors = correct_index_distribution_errors([*existing, *accepted_records]) if accepted_records else []
        if distribution_errors:
            for target, data, decision in accepted_candidates:
                review = CandidateDecision("review", decision.validation, False, distribution_errors, decision.review_score, distribution_errors)
                self._queue_review(target, data, review)
            stats.queued_for_review += len(accepted_records)
            stats.accepted = 0
            stats.correct_index_distribution_generated = {str(index): 0 for index in range(4)}
            accepted_records.clear()
        for record in accepted_records:
            _jsonl_append(self.config.question_bank_path, record)
            _jsonl_append(self.config.provenance_path, {"id": record["id"], **record["provenance"]})

        stats.api_call_count = int(getattr(self.generator, "api_call_count", stats.generated))
        stats.rate_limit_errors = int(getattr(self.generator, "rate_limit_errors", 0))
        if self.config.update_meta and (
            stats.generated or stats.accepted or stats.queued_for_review or stats.rejected or stats.duplicates
        ):
            total_questions = len(existing) + len(accepted_records)
            meta.meta["bank_version"] = self.config.bank_version
            meta.meta["content_factory"] = {
                "last_refill_at": _now_iso(),
                "model": self.config.model_name,
                "bank_version": self.config.bank_version,
                "mode": self.config.mode,
                "prompt_version": self.config.prompt_version,
                "target_accepts": self.config.target_accepts,
                "generated": stats.generated,
                "accepted": stats.accepted,
                "queued_for_review": stats.queued_for_review,
                "rejected": stats.rejected,
                "duplicates": stats.duplicates,
                "errors": stats.errors,
                "targets": stats.targets,
                "api_call_count": stats.api_call_count,
                "rate_limit_errors": stats.rate_limit_errors,
                "correct_index_distribution_generated": stats.correct_index_distribution_generated,
            }
            meta.meta["question_bank"] = {
                "path": str(self.config.question_bank_path),
                "bank_version": self.config.bank_version,
                "total_questions": total_questions,
                "updated_at": _now_iso(),
            }
            if accepted_records:
                meta.meta["last_chapter_id"] = str(accepted_records[-1].get("chapter_id", ""))
            meta.save()

        return stats

    def _queue_review(self, target: CoverageTarget, data: Dict[str, Any], decision: CandidateDecision) -> None:
        _jsonl_append(
            self.config.review_queue_path,
            {
                "queued_at": _now_iso(),
                "target": target.__dict__,
                "candidate": data,
                "decision": decision.action,
                "score": decision.validation.score,
                "reasons": decision.reasons,
                "review_score": decision.review_score,
                "review_reasons": decision.review_reasons,
                "prompt_version": self.config.prompt_version,
                "model": self.config.model_name,
                "bank_version": self.config.bank_version,
            },
        )

    def _queue_error(self, target: CoverageTarget, error: str) -> None:
        _jsonl_append(
            self.config.review_queue_path,
            {
                "queued_at": _now_iso(),
                "target": target.__dict__,
                "decision": "error",
                "error": error,
                "prompt_version": self.config.prompt_version,
                "model": self.config.model_name,
                "bank_version": self.config.bank_version,
            },
        )


def _is_transient_generation_error(error: str) -> bool:
    text = error.lower()
    return "429" in text or "rate limit" in text or "quota" in text or "resource exhausted" in text
