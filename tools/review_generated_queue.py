from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gtest_quiz.bank_epoch import current_bank_version, current_model_name
from gtest_quiz.content_factory import (
    BalancedAnswerPositions,
    CoverageTarget,
    FactoryConfig,
    GeneratedQuestionSpec,
    PROVENANCE_PATH,
    QUESTION_BANK_PATH,
    REVIEW_QUEUE_PATH,
    _jsonl_append,
    decide_candidate,
    load_existing_items,
    to_question_record,
)
from gtest_quiz.question_quality import build_duplicate_index, correct_index_distribution_errors

REVIEW_ARCHIVE_DIR = Path("bank/review_archive")


def load_queue(path: Path = REVIEW_QUEUE_PATH) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            items.append(json.loads(raw))
    return items


def summarize_queue(path: Path = REVIEW_QUEUE_PATH) -> Dict[str, Any]:
    items = load_queue(path)
    by_decision: Dict[str, int] = {}
    reject_reasons: Dict[str, int] = {}
    for item in items:
        decision = str(item.get("decision", "unknown"))
        by_decision[decision] = by_decision.get(decision, 0) + 1
        if decision == "reject":
            for reason in item.get("reasons", []) or []:
                reason = str(reason)
                reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
    return {
        "total": len(items),
        "by_decision": dict(sorted(by_decision.items())),
        "review_queue_total": len(items),
        "review_queue_by_decision": dict(sorted(by_decision.items())),
        "reject_reason_top": [
            {"reason": reason, "count": count}
            for reason, count in sorted(reject_reasons.items(), key=lambda pair: (-pair[1], pair[0]))[:8]
        ],
    }


def archive_rejects(*, queue_path: Path = REVIEW_QUEUE_PATH, archive_dir: Path = REVIEW_ARCHIVE_DIR, keep_recent: int = 100) -> Dict[str, Any]:
    items = load_queue(queue_path)
    reject_items = [item for item in items if str(item.get("decision", "")) == "reject"]
    if len(reject_items) <= keep_recent:
        return {"archived": 0, "remaining": len(items)}
    archive_dir.mkdir(parents=True, exist_ok=True)
    cutoff = len(reject_items) - keep_recent
    reject_to_archive = reject_items[:cutoff]
    archive_path = archive_dir / f"rejects-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    archive_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in reject_to_archive),
        encoding="utf-8",
    )
    archived_ids = {id(item) for item in reject_to_archive}
    remaining = [item for item in items if id(item) not in archived_ids]
    queue_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in remaining), encoding="utf-8")
    return {"archived": len(reject_to_archive), "remaining": len(remaining), "archive_path": str(archive_path)}


def promote(
    index: int,
    *,
    queue_path: Path = REVIEW_QUEUE_PATH,
    bank_path: Path = QUESTION_BANK_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> Dict[str, Any]:
    items = load_queue(queue_path)
    if index < 0 or index >= len(items):
        raise IndexError("queue index out of range")
    item = items[index]
    candidate = GeneratedQuestionSpec.model_validate(item.get("candidate"), strict=True).model_dump()
    target_data = item.get("target", {})
    target = CoverageTarget(
        chapter_group=str(target_data.get("chapter_group", "")),
        chapter_id=str(target_data.get("chapter_id", candidate["syllabus_node"])),
        current_count=0, target_count=1, priority=1,
        desired_difficulty=str(target_data.get("desired_difficulty", candidate["difficulty"])),
    )
    if item.get("model") != current_model_name() or item.get("bank_version") != current_bank_version():
        raise ValueError("candidate model/bank version does not match the active bank")
    existing = load_existing_items(bank_path)
    decision = decide_candidate(
        candidate, duplicate_index=build_duplicate_index(existing), target=target,
        min_explanation_length=120, review_threshold=95,
    )
    if decision.action != "accept":
        raise ValueError(f"candidate is not promotable: {decision.reasons}")
    record = to_question_record(
        candidate, target=target,
        config=FactoryConfig(model_name=item["model"], bank_version=item["bank_version"], prompt_version=item["prompt_version"]),
        decision=decision, desired_correct_index=BalancedAnswerPositions(existing).take(),
    )
    record["source"] = "review_promoted"
    record["provenance"]["review_queue_index"] = index
    errors = correct_index_distribution_errors([*existing, record])
    if errors:
        raise ValueError("candidate cannot be published yet: " + "; ".join(errors))
    _jsonl_append(bank_path, record)
    _jsonl_append(provenance_path, {"id": record["id"], **record["provenance"]})
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or promote generated question review queue")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--promote", type=int)
    parser.add_argument("--archive-rejects", action="store_true")
    parser.add_argument("--keep-recent", type=int, default=100)
    args = parser.parse_args()
    if args.promote is not None:
        print(json.dumps(promote(args.promote), ensure_ascii=False, indent=2))
    elif args.archive_rejects:
        print(json.dumps(archive_rejects(keep_recent=args.keep_recent), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summarize_queue(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
