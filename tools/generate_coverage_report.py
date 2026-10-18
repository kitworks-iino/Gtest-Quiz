from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gtest_quiz.bank_epoch import current_bank_version, current_model_name
from gtest_quiz.question_quality import (add_question_to_index, build_duplicate_index, correct_index_window_issues, is_probable_duplicate)


BANK_PATH = Path("bank/question_bank.jsonl")
META_PATH = Path("bank/meta.json")
REVIEW_QUEUE_PATH = Path("bank/generated_review_queue.jsonl")
COVERAGE_REPORT_PATH = Path("bank/coverage_report.json")
DIFFICULTIES = ("basic", "standard", "advanced")
LEGAL_MARKERS = ("法律", "倫理", "ガバナンス", "個人情報", "著作権", "知的財産", "ガイドライン", "規制")
LEGAL_SOURCE_FIELDS = (
    "source_url",
    "source_title",
    "source_version",
    "source_checked_at",
    "legal_basis",
)
EXTENDED_LEGAL_SOURCE_FIELDS = (
    *LEGAL_SOURCE_FIELDS,
    "source_quote_short",
    "source_section",
    "source_organization",
)

PROFILE_RULES: Dict[str, Dict[str, int]] = {
    "bootstrap": {"min_questions": 1, "min_chapters": 1, "min_per_chapter": 0},
    "alpha": {"min_questions": 100, "min_chapters": 20, "min_per_chapter": 0},
    "beta": {"min_questions": 275, "min_chapters": 55, "min_per_chapter": 5},
    "complete": {"min_questions": 550, "min_chapters": 55, "min_per_chapter": 10},
    "expanded": {"min_questions": 1000, "min_chapters": 55, "min_per_chapter": 15},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            rows.append(json.loads(raw))
    return rows


def expected_chapters(meta_path: Path = META_PATH) -> List[Dict[str, str]]:
    if not meta_path.exists():
        return []
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    chapters: List[Dict[str, str]] = []
    for _group_key, group in (meta.get("chapters") or {}).items():
        group_label = str(group.get("label", ""))
        subchapters = group.get("subchapters") or {}
        if not isinstance(subchapters, dict):
            continue
        for _sub_key, sub in subchapters.items():
            label = str(sub.get("label", "")).strip()
            if label:
                chapters.append({"chapter_group": group_label, "chapter_id": label})
    return chapters


def is_legal_row(row: Dict[str, Any]) -> bool:
    if any(str(row.get(field, "")).strip() for field in EXTENDED_LEGAL_SOURCE_FIELDS):
        return True
    text = " ".join(str(row.get(key, "")) for key in ("domain", "chapter_group", "chapter_id"))
    return any(marker in text for marker in LEGAL_MARKERS)


def _counter_dict(values: Iterable[Any], keys: Iterable[Any]) -> Dict[str, int]:
    counter = Counter(values)
    return {str(key): int(counter.get(key, 0)) for key in keys}


def _review_queue_summary(path: Path = REVIEW_QUEUE_PATH) -> Dict[str, Any]:
    rows = load_jsonl(path)
    decisions = Counter(str(row.get("decision", "unknown")) for row in rows)
    reasons: Counter[str] = Counter()
    for row in rows:
        for reason in row.get("reasons", []) or []:
            reasons[str(reason)] += 1
        if row.get("error"):
            reasons[str(row.get("error"))] += 1
    return {
        "review_queue_total": len(rows),
        "review_queue_by_decision": dict(sorted(decisions.items())),
        "reject_reason_top": [
            {"reason": reason, "count": count}
            for reason, count in reasons.most_common(8)
        ],
    }


def _duplicate_suspect_count(rows: List[Dict[str, Any]]) -> int:
    duplicate_index = build_duplicate_index([])
    count = 0
    for row in rows:
        if is_probable_duplicate(str(row.get("question", "")), duplicate_index):
            count += 1
        add_question_to_index(duplicate_index, str(row.get("question", "")))
    return count


def _concrete_model(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    model = value.strip()
    return model if model and model.casefold() != "auto" else None


def _model_report(rows: List[Dict[str, Any]], meta_path: Path) -> Dict[str, Any]:
    policy = current_model_name()
    models = [
        _concrete_model(row["provenance"].get("model"))
        if isinstance(row.get("provenance"), dict) else None
        for row in rows
    ]
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    last_result = meta.get("last_refill_result") or {}
    last_selection = last_result.get("model_selection") or {}
    last_selected_model = (
        _concrete_model(last_selection.get("model_id"))
        or _concrete_model(last_result.get("model"))
    )
    last_generation_model = None
    for result in (last_result, meta.get("content_factory") or {}):
        # Resolving a model, or stopping before generation, is not generation.
        if result.get("generated", 0) > 0:
            last_generation_model = _concrete_model(result.get("model"))
            if last_generation_model:
                break
    return {
        "model": last_generation_model,
        "model_selection_policy": policy,
        "last_selected_model": last_selected_model,
        "last_model_selection": last_selection or None,
        "model_distribution": dict(sorted(Counter(model for model in models if model).items())),
        "model_provenance_missing_count": sum(model is None for model in models),
        # Historical provenance remains valid when the selected model changes.
        # A fixed-policy mismatch is diagnostic, not a quality failure.
        "model_mismatch_count": (
            0 if policy.casefold() == "auto"
            else sum(model != policy for model in models if model)
        ),
    }



def difficulty_balance_ok(distribution: Dict[str, int], min_per_chapter: int) -> bool:
    total = sum(distribution.values())
    if total < min_per_chapter or min_per_chapter < 10:
        return True
    if min_per_chapter >= 15:
        return distribution.get("basic", 0) >= 3 and distribution.get("standard", 0) >= 5 and distribution.get("advanced", 0) >= 3
    return distribution.get("basic", 0) >= 2 and distribution.get("standard", 0) >= 3 and distribution.get("advanced", 0) >= 2


def build_coverage_report(
    *,
    bank_path: Path = BANK_PATH,
    meta_path: Path = META_PATH,
    review_queue_path: Path = REVIEW_QUEUE_PATH,
) -> Dict[str, Any]:
    rows = load_jsonl(bank_path)
    expected = expected_chapters(meta_path)
    expected_ids = [chapter["chapter_id"] for chapter in expected]
    rows_by_chapter: Dict[str, List[Dict[str, Any]]] = {chapter_id: [] for chapter_id in expected_ids}
    extra_chapters: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        chapter_id = str(row.get("chapter_id", ""))
        if chapter_id in rows_by_chapter:
            rows_by_chapter[chapter_id].append(row)
        else:
            extra_chapters.setdefault(chapter_id, []).append(row)

    chapter_reports: List[Dict[str, Any]] = []
    for chapter in expected:
        chapter_id = chapter["chapter_id"]
        chapter_rows = rows_by_chapter.get(chapter_id, [])
        difficulty_distribution = _counter_dict((row.get("difficulty") for row in chapter_rows), DIFFICULTIES)
        correct_distribution = _counter_dict((row.get("correct_index") for row in chapter_rows), range(4))
        legal_source_missing = 0
        review_warning_count = 0
        for row in chapter_rows:
            provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
            if provenance.get("review_reasons") or provenance.get("accepted_with_review_warnings") is not False:
                review_warning_count += 1
            if is_legal_row(row):
                legal_source_missing += sum(1 for field in LEGAL_SOURCE_FIELDS if not str(row.get(field, "")).strip())
        chapter_reports.append(
            {
                "chapter_group": chapter["chapter_group"],
                "chapter_id": chapter_id,
                "question_count": len(chapter_rows),
                "difficulty_distribution": difficulty_distribution,
                "correct_index_distribution": correct_distribution,
                "legal_source_missing": legal_source_missing,
                "review_warning_count": review_warning_count,
            }
        )

    missing_chapters = [item["chapter_id"] for item in chapter_reports if item["question_count"] == 0]
    active_review_warning_count = sum(
        1
        for row in rows
        if (isinstance(row.get("provenance"), dict) and (row["provenance"].get("review_reasons") or row["provenance"].get("accepted_with_review_warnings") is not False))
    )
    legal_source_missing = sum(item["legal_source_missing"] for item in chapter_reports)
    duplicate_suspect_count = _duplicate_suspect_count(rows)
    correct_distribution = _counter_dict((row.get("correct_index") for row in rows), range(4))
    model_report = _model_report(rows, meta_path)
    bank_version_mismatch_count = sum(1 for row in rows if row.get("bank_version") != current_bank_version())
    review_queue = _review_queue_summary(review_queue_path)

    window_issues = correct_index_window_issues(rows)
    profiles: Dict[str, Dict[str, Any]] = {}
    for profile, rule in PROFILE_RULES.items():
        min_per_chapter = rule["min_per_chapter"]
        chapter_floor_count = sum(1 for item in chapter_reports if item["question_count"] >= min_per_chapter) if min_per_chapter else 0
        difficulty_failures = [
            item["chapter_id"]
            for item in chapter_reports
            if item["question_count"] >= min_per_chapter and not difficulty_balance_ok(item["difficulty_distribution"], min_per_chapter)
        ]
        passed = (
            len(rows) >= rule["min_questions"]
            and len(chapter_reports) - len(missing_chapters) >= rule["min_chapters"]
            and (not min_per_chapter or chapter_floor_count == len(chapter_reports))
            and active_review_warning_count == 0
            and legal_source_missing == 0
            and duplicate_suspect_count == 0
            and model_report["model_provenance_missing_count"] == 0
            and bank_version_mismatch_count == 0
            and not window_issues
            and not difficulty_failures
        )
        profiles[profile] = {
            "passed": passed,
            "min_questions": rule["min_questions"],
            "min_chapters": rule["min_chapters"],
            "min_per_chapter": min_per_chapter,
            "chapters_meeting_floor": chapter_floor_count,
            "difficulty_balance_failures": difficulty_failures,
        }

    return {
        "schema_version": "gtest_quiz_coverage_report_v1",
        "generated_at": _now_iso(),
        "bank_version": current_bank_version(),
        **model_report,
        "total_questions": len(rows),
        "target_complete_questions": PROFILE_RULES["complete"]["min_questions"],
        "target_expanded_questions": PROFILE_RULES["expanded"]["min_questions"],
        "expected_chapters": len(chapter_reports),
        "covered_chapters": len(chapter_reports) - len(missing_chapters),
        "missing_chapters": missing_chapters,
        "extra_chapters": sorted(chapter for chapter, items in extra_chapters.items() if items),
        "correct_index_distribution": correct_distribution,
        "correct_index_window_issues": window_issues,
        "active_review_warning_count": active_review_warning_count,
        "legal_source_missing": legal_source_missing,
        "duplicate_suspect_count": duplicate_suspect_count,
        "bank_version_mismatch_count": bank_version_mismatch_count,
        "chapters": chapter_reports,
        "profiles": profiles,
        **review_queue,
    }


def write_coverage_report(path: Path = COVERAGE_REPORT_PATH) -> Dict[str, Any]:
    report = build_coverage_report()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate bank coverage and readiness report")
    parser.add_argument("--check-profile", choices=sorted(PROFILE_RULES), default="")
    parser.add_argument("--output", default=str(COVERAGE_REPORT_PATH))
    args = parser.parse_args()
    report = write_coverage_report(Path(args.output))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.check_profile and not report["profiles"][args.check_profile]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
