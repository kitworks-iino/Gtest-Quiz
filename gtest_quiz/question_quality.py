"""Question quality validation and near-duplicate detection utilities."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Set


WHITESPACE_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[\u3000\s、。，．,.;:!?！？()（）\[\]【】『』「」'\"`]+")


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    score: int
    reasons: List[str]


@dataclass(frozen=True)
class DuplicateIndex:
    exact_hashes: Set[str]
    normalized_hashes: Set[str]
    fingerprints: Set[str]


def _normalize_space(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text.strip())


def normalize_question_text(text: str) -> str:
    text = _normalize_space(text)
    text = PUNCT_RE.sub("", text)
    return text.lower()


def exact_question_hash(text: str) -> str:
    return hashlib.sha256(_normalize_space(text).encode("utf-8")).hexdigest()


def normalized_question_hash(text: str) -> str:
    return hashlib.sha256(normalize_question_text(text).encode("utf-8")).hexdigest()


def fingerprint_tokens(text: str, n: int = 18) -> str:
    normalized = normalize_question_text(text)
    if not normalized:
        return ""
    tokens = [t for t in re.split(r"[^0-9a-zA-Z一-龥ぁ-んァ-ヶ]+", normalized) if t]
    if not tokens:
        return normalized[:80]
    return "|".join(tokens[:n])


def build_duplicate_index(items: Iterable[Dict[str, Any]]) -> DuplicateIndex:
    exact_hashes: Set[str] = set()
    normalized_hashes: Set[str] = set()
    fingerprints: Set[str] = set()

    for item in items:
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        exact_hashes.add(exact_question_hash(question))
        normalized_hashes.add(normalized_question_hash(question))
        fingerprints.add(fingerprint_tokens(question))

    return DuplicateIndex(
        exact_hashes=exact_hashes,
        normalized_hashes=normalized_hashes,
        fingerprints=fingerprints,
    )


def add_question_to_index(index: DuplicateIndex, question: str) -> None:
    """Update the index once per accepted row instead of rehashing the bank."""
    if question.strip():
        index.exact_hashes.add(exact_question_hash(question))
        index.normalized_hashes.add(normalized_question_hash(question))
        index.fingerprints.add(fingerprint_tokens(question))


# Keep these conservative patterns aligned with frontend/src/content-guard.js.
POSITIONAL_CHOICE_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"(?:選択肢|正答肢|誤答肢)\s*(?:の\s*)?(?:\(\s*)?[0-4A-D](?![A-Za-z0-9つ個件])",
    r"(?:正解|不正解|誤答)(?:は)?\s*(?:\(\s*)?インデックス\s*[0-4](?![0-9])",
    r"インデックス\s*[0-4](?![0-9])(?:が正解|の選択肢|は誤り)",
    r"(?:最初|最後|[1-4一二三四](?:つ目|番目))の選択肢",
    r"選択肢の[1-4一二三四](?:つ目|番目)",
    r"(?:他の選択肢|不正解(?:の選択肢)?|誤答)(?:について)?\s*[、,:：]\s*[0-4A-D](?:番目|つ目)?(?:\s*(?:・|、|と|や|および|及び)\s*[0-4A-D](?:番目|つ目)?)*(?=は|も|の|について)",
))


def has_positional_choice_reference(explanation: str) -> bool:
    text = unicodedata.normalize("NFKC", explanation)
    return any(pattern.search(text) for pattern in POSITIONAL_CHOICE_PATTERNS)


def correct_index_window_issues(rows: Sequence[Dict[str, Any]], window_size: int = 100) -> List[Dict[str, Any]]:
    issues = []
    for start in range(0, len(rows), window_size):
        window = rows[start : start + window_size]
        if len(window) < 20:
            continue
        counts = Counter(row.get("correct_index") for row in window)
        expected = len(window) / 4
        if any(counts[index] < expected * 0.5 or counts[index] > expected * 1.5 for index in range(4)):
            issues.append({
                "start": start + 1,
                "end": start + len(window),
                "correct_index_distribution": {str(index): counts[index] for index in range(4)},
            })
    return issues


def correct_index_distribution_errors(rows: Sequence[Dict[str, Any]]) -> List[str]:
    """Shared publication gate; thresholds match the existing bank validator."""
    if len(rows) < 4:
        return []
    counts = Counter(row.get("correct_index") for row in rows)
    errors = []
    missing = [index for index in range(4) if counts[index] == 0]
    if missing:
        errors.append(f"correct_index distribution has empty buckets: {missing}; counts={dict(counts)}")
    values = [counts[index] for index in range(4)]
    if len(rows) >= 20 and max(values) - min(values) > max(5, len(rows) // 4):
        errors.append(f"correct_index distribution is too skewed: counts={dict(counts)}")
    for issue in correct_index_window_issues(rows):
        errors.append(f"correct_index distribution is skewed in rows {issue['start']}-{issue['end']}: counts={issue['correct_index_distribution']}")
    return errors


def is_probable_duplicate(question: str, duplicate_index: DuplicateIndex) -> bool:
    exact_hash = exact_question_hash(question)
    if exact_hash in duplicate_index.exact_hashes:
        return True

    normalized_hash = normalized_question_hash(question)
    if normalized_hash in duplicate_index.normalized_hashes:
        return True

    fp = fingerprint_tokens(question)
    return bool(fp and fp in duplicate_index.fingerprints)


def _has_duplicate_choices(choices: Sequence[str]) -> bool:
    normalized = [normalize_question_text(choice) for choice in choices]
    normalized = [c for c in normalized if c]
    return len(normalized) != len(set(normalized))


def validate_generated_question(data: Dict[str, Any], min_explanation_length: int = 120) -> ValidationResult:
    reasons: List[str] = []
    score = 100

    question = str(data.get("question", "")).strip()
    choices = data.get("choices", [])
    explanation = str(data.get("explanation", "")).strip()
    difficulty = str(data.get("difficulty", "")).strip()

    try:
        correct_index = int(data.get("correct_index", -1))
    except Exception:
        correct_index = -1

    if not question:
        reasons.append("question is empty")
        score -= 50
    elif len(question) < 18:
        reasons.append("question is too short")
        score -= 20

    if not isinstance(choices, list) or len(choices) != 4:
        reasons.append("choices must contain exactly four items")
        score -= 50
        choices = []
    else:
        if any(not str(choice).strip() for choice in choices):
            reasons.append("choices contain empty text")
            score -= 25
        if _has_duplicate_choices([str(choice) for choice in choices]):
            reasons.append("choices are duplicated or near-duplicated")
            score -= 25
        if any(len(str(choice).strip()) < 2 for choice in choices):
            reasons.append("choice text is too short")
            score -= 10

    if correct_index not in {0, 1, 2, 3}:
        reasons.append("correct_index must be 0..3")
        score -= 40

    if len(explanation) < min_explanation_length:
        reasons.append("explanation is too short")
        score -= 20
    if explanation and not any(token in explanation for token in ("正解理由", "正解は", "適切です", "根拠")):
        reasons.append("explanation should include the reason for the correct answer")
        score -= 10
    if explanation and not any(token in explanation for token in ("不正解理由", "他の選択肢", "誤答", "差分")):
        reasons.append("explanation should distinguish incorrect choices")
        score -= 10

    if difficulty not in {"basic", "standard", "advanced"}:
        reasons.append("difficulty must be basic|standard|advanced")
        score -= 10

    if question and explanation and normalize_question_text(question) == normalize_question_text(explanation):
        reasons.append("explanation duplicates the question")
        score -= 20

    return ValidationResult(
        is_valid=(score >= 75 and not any(r.startswith("choices must") or r == "question is empty" for r in reasons)),
        score=max(score, 0),
        reasons=reasons,
    )
