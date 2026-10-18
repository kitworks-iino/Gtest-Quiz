"""Read and preserve question-bank metadata used by the generation pipeline."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .quota import QuotaManager


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MetaManager:
    def __init__(self, path: str = "bank/meta.json") -> None:
        self.path = Path(path)
        self.meta: dict[str, Any] = {}
        self.quota = QuotaManager(self.meta)

    def load(self) -> None:
        if self.path.exists():
            self.meta = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.meta, dict):
                raise ValueError("Bank metadata must be a JSON object")
        else:
            self.meta = {}
        self.meta.setdefault("version", 1)
        self.meta.setdefault("created_at", _now_iso())
        self.meta.setdefault("chapters", {})
        self.quota = QuotaManager(self.meta)

    def save(self) -> None:
        if not self.meta:
            return
        self.meta["updated_at"] = _now_iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
