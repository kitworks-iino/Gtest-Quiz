"""Retain the existing local quota stop; this is not an API billing meter."""

from __future__ import annotations

from typing import Any


class QuotaManager:
    def __init__(self, meta: dict[str, Any]) -> None:
        self.estimate = meta.get("quota_estimate", {})

    def is_near_limit(self, threshold: float = 0.9) -> bool:
        limit = self.estimate.get("estimated_limit_tokens")
        used = self.estimate.get("total_used_tokens", 0)
        if not isinstance(limit, (int, float)) or limit <= 0:
            return False
        return isinstance(used, (int, float)) and used / limit >= threshold
