from __future__ import annotations

import os


DEFAULT_GEMINI_MODEL = "auto"
DEFAULT_QUESTION_BANK_EPOCH = "gemini35_v1"


def current_model_name() -> str:
    return os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL


def current_bank_version() -> str:
    return os.getenv("QUESTION_BANK_EPOCH", DEFAULT_QUESTION_BANK_EPOCH).strip() or DEFAULT_QUESTION_BANK_EPOCH
