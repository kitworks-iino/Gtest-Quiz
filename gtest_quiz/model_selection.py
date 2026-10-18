"""Discover a current text model without silently crossing into paid-only models.

Free-tier eligibility is not the project's billing tier. A project linked to a
paid billing account can still incur charges when using these models.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
from typing import Any, Callable, Iterable
from urllib.parse import urlparse
from urllib.request import Request, urlopen


PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing?hl=en"
_MAX_PRICING_BYTES = 2_000_000
_STABLE_TEXT_MODEL = re.compile(
    r"gemini-(\d+(?:\.\d+){0,2})-(argon|pro|flash|flash-lite)(?:-(\d{3}))?"
)
_FAMILY_RANK = {"flash-lite": 0, "flash": 1, "pro": 2, "argon": 3}


class ModelSelectionError(RuntimeError):
    """No verified model can be selected; generation must not proceed."""


@dataclass(frozen=True)
class ModelSelection:
    model_id: str
    eligible_models: tuple[str, ...]
    checked_at: str
    pricing_url: str
    reason: str


def _text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _model_rank(model_id: str) -> tuple[int, ...] | None:
    """A declared version/family policy, not a benchmark quality guarantee."""
    match = _STABLE_TEXT_MODEL.fullmatch(model_id)
    if not match:
        return None
    version, family, revision = match.groups()
    numbers = tuple(int(part) for part in version.split("."))
    return (*numbers, *((0,) * (3 - len(numbers))), _FAMILY_RANK[family], int(revision or 0))


def _has_free_standard_io(rows: list[list[str]]) -> bool:
    if not rows or rows[0].count("free tier") != 1:
        return False
    free_column = rows[0].index("free tier")
    if free_column == 0:
        return False
    prices: dict[str, list[str]] = {"input": [], "output": []}
    for row in rows[1:]:
        if not row or len(row) != len(rows[0]):
            return False
        for direction in prices:
            if row[0] == f"{direction} price" or row[0].startswith(f"{direction} price ("):
                prices[direction].append(row[free_column])
    # Missing, duplicate, conditional, or ambiguous prices never authorize use.
    return all(values == ["free of charge"] for values in prices.values())


class _PricingParser(HTMLParser):
    """Parse model headings and Standard tables; never inherit Batch prices."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.decisions: dict[str, list[bool]] = {}
        self._div_depth = 0
        self._heading_depth: int | None = None
        self._models: list[str] = []
        self._tier = ""
        self._code: list[str] | None = None
        self._h3: list[str] | None = None
        self._rows: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._ambiguous = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "div":
            self._div_depth += 1
            if "heading-group" in (attributes.get("class") or "").split():
                self._heading_depth = self._div_depth
        elif tag == "h2":
            self._models = []
            self._tier = ""
        elif tag == "code" and self._heading_depth is not None:
            self._code = []
        elif tag == "h3":
            self._h3 = []
        elif tag == "table" and self._models and self._tier == "standard":
            self._rows = []
            self._ambiguous = False
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []
            if any(attributes.get(key, "1") != "1" for key in ("colspan", "rowspan")):
                self._ambiguous = True
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        for target in (self._code, self._h3, self._cell):
            if target is not None:
                target.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            if self._heading_depth == self._div_depth:
                self._heading_depth = None
            self._div_depth -= 1
        elif tag == "code" and self._code is not None:
            model_id = "".join(self._code).strip()
            if _model_rank(model_id) is not None:
                self._models.append(model_id)
            self._code = None
        elif tag == "h3" and self._h3 is not None:
            self._tier = _text("".join(self._h3))
            self._h3 = None
        elif tag in {"th", "td"} and self._cell is not None:
            if self._row is not None:
                self._row.append(_text("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._rows is not None:
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._rows is not None:
            free = not self._ambiguous and _has_free_standard_io(self._rows)
            for model_id in self._models:
                self.decisions.setdefault(model_id, []).append(free)
            self._rows = None
            self._row = None
            self._cell = None


def free_standard_models(html: str) -> frozenset[str]:
    """Return only explicitly free Standard text input AND output models."""
    if not isinstance(html, str) or not html.strip():
        raise ModelSelectionError("The official pricing response was empty or invalid.")
    parser = _PricingParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        raise ModelSelectionError("The official pricing page could not be parsed.") from exc
    if not parser.decisions:
        raise ModelSelectionError("No recognized Standard text-model pricing tables were found.")
    # Conflicting duplicate sections also fail closed for that model.
    return frozenset(model_id for model_id, decisions in parser.decisions.items() if all(decisions))


def fetch_official_pricing() -> str:
    request = Request(PRICING_URL, headers={"Accept": "text/html", "Accept-Language": "en"})
    try:
        with urlopen(request, timeout=30) as response:
            final_url = urlparse(response.geturl())
            if final_url.scheme != "https" or final_url.hostname != "ai.google.dev":
                raise ModelSelectionError("Pricing redirected outside the official HTTPS source.")
            if response.headers.get_content_type() != "text/html":
                raise ModelSelectionError("The official pricing response is not HTML.")
            body = response.read(_MAX_PRICING_BYTES + 1)
            if len(body) > _MAX_PRICING_BYTES:
                raise ModelSelectionError("The official pricing response exceeded the size limit.")
            return body.decode("utf-8")
    except ModelSelectionError:
        raise
    except Exception as exc:
        raise ModelSelectionError("The official pricing page could not be retrieved.") from exc


def _field(model: Any, name: str) -> Any:
    return model.get(name) if isinstance(model, dict) else getattr(model, name, None)


def resolve_model(
    client: Any,
    requested: str = "auto",
    *,
    fetch_pricing: Callable[[], str] = fetch_official_pricing,
    list_models: Callable[[], Iterable[Any]] | None = None,
    checked_at: datetime | None = None,
) -> ModelSelection:
    """Intersect live API availability with current official free-tier prices.

    Only stable, recognized general text families are ranked, by numeric version
    then family (Argon, Pro, Flash, Flash-Lite). The SDK pager is fully consumed.
    Neither this function nor its fallbacks make a generation request.
    """
    requested = requested.strip() or "auto"
    if requested.startswith("models/"):
        requested = requested.removeprefix("models/")
    try:
        html = fetch_pricing()
    except ModelSelectionError:
        raise
    except Exception as exc:
        raise ModelSelectionError("The official pricing page could not be retrieved.") from exc
    free_models = free_standard_models(html)
    available: set[str] = set()
    try:
        for model in (list_models or client.models.list)():
            name = _field(model, "name")
            actions = _field(model, "supported_actions")
            if not isinstance(name, str) or not isinstance(actions, (list, tuple)):
                continue
            name = name.removeprefix("models/")
            if "generateContent" in actions and _model_rank(name) is not None:
                available.add(name)
    except Exception as exc:
        raise ModelSelectionError("The available Gemini models could not be listed.") from exc
    eligible = tuple(sorted(free_models & available, key=lambda name: (_model_rank(name), name), reverse=True))
    if not eligible:
        raise ModelSelectionError("No available stable text model has verified free Standard input and output.")
    if requested != "auto" and requested not in eligible:
        raise ModelSelectionError(
            f"Requested model {requested!r} is unavailable, unrecognized, or lacks verified free Standard pricing."
        )
    chosen = eligible[0] if requested == "auto" else requested
    timestamp = checked_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ModelSelectionError("The pricing-check timestamp must include a timezone.")
    return ModelSelection(
        model_id=chosen,
        eligible_models=eligible,
        checked_at=timestamp.astimezone(timezone.utc).isoformat(),
        pricing_url=PRICING_URL,
        reason=(
            "Latest numeric version, then Argon > Pro > Flash > Flash-Lite; "
            "stable text models with generateContent and verified free Standard input/output only. "
            "This verifies free-tier eligibility, not the project's billing tier or benchmark superiority."
            if requested == "auto" else "Explicit model validated against API availability and free Standard input/output pricing."
        ),
    )
