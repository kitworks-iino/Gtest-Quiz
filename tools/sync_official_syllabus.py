"""Refresh the JDLA-published syllabus outline without AI or paid services.

Only a fully validated fetch replaces the last good snapshot.  The signature
includes the PDF bytes so a changed document also invalidates reviewed guidance
when its public outline happens to stay the same.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SOURCE_URL = "https://www.jdla.org/certificate/general/"
SCHEMA_VERSION = "gtest_official_syllabus_v1"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "frontend/src/official-syllabus.json"
HTML_LIMIT = 4 * 1024 * 1024
PDF_LIMIT = 20 * 1024 * 1024


class SyllabusError(ValueError):
    """The official response could not be validated safely."""


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    parent: Element | None = field(default=None, repr=False)
    children: list = field(default_factory=list)

    def text(self) -> str:
        return " ".join("".join(c if isinstance(c, str) else c.text()
                                for c in self.children).split())

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.walk()


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Element("root", {})
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Element(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def official_url(value: str, base: str = SOURCE_URL) -> str:
    """Allow only HTTPS JDLA URLs, including each redirect destination."""
    url = urljoin(base, value)
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc not in {"www.jdla.org", "jdla.org"}
            or any(ord(char) < 32 for char in url)):
        raise SyllabusError("Syllabus link is not an official JDLA HTTPS URL")
    return url


def stable_document_url(value: str, base: str) -> str:
    parts = urlsplit(official_url(value, base))
    # WordPress adds a changing refresh token which is not needed for downloads.
    query = [(key, val) for key, val in parse_qsl(parts.query) if key != "refresh"]
    if not parts.path.lower().endswith(".pdf") and not any(
        key == "wpdmdl" and val.isdigit() for key, val in query
    ):
        raise SyllabusError("No official PDF download was found")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, official_url(newurl, req.full_url))


def fetch(url: str, limit: int) -> bytes:
    request = Request(official_url(url), headers={"User-Agent": "Gtest-Quiz/1.0 (official syllabus sync)"})
    with build_opener(OfficialRedirects()).open(request, timeout=30) as response:
        official_url(response.url)
        content = response.read(limit + 1)
    if not content or len(content) > limit:
        raise SyllabusError("Official response is empty or exceeds the size limit")
    return content


def parse_outline(html: str) -> dict:
    elements = list(Document(html).root.walk())
    start = next((i for i, node in enumerate(elements)
                  if node.tag == "h2" and node.attrs.get("id") == "general_No03"), None)
    if start is None:
        raise SyllabusError("Official syllabus section is missing")
    end = next((i for i in range(start + 1, len(elements)) if elements[i].tag == "h2"), len(elements))
    elements = elements[start + 1:end]
    sections, candidates = [], []
    category = ""
    for node in elements:
        value = node.text()
        if node.tag == "h3" and "分野】" in value:
            category = value.strip("【】")
        elif node.tag == "h3" and "ac-item" in node.attrs.get("class", "").split():
            sections.append({"id": f"s{len(sections) + 1:02d}", "title": value,
                             "category": category, "topics": []})
        elif node.tag == "h4" and sections:
            sections[-1]["topics"].append(value)
        elif node.tag == "a" and "G検定" in value and "シラバス" in value:
            # The official page also publishes an explicitly obsolete syllabus.
            container = node.parent
            while container and "wp-block-column" not in container.attrs.get("class", "").split():
                container = container.parent
            context = container.text() if container else value
            if "旧シラバス" in context or "まで" in value or "旧" in value:
                continue
            if "から適用" not in context and "～" not in value and "以降" not in context:
                continue
            candidates.append({"revision": value, "download_page_url": official_url(node.attrs.get("href", ""))})
    if len(candidates) != 1:
        raise SyllabusError("Current syllabus download cannot be identified unambiguously")
    if not 8 <= len(sections) <= 30 or not 30 <= sum(len(s["topics"]) for s in sections) <= 200:
        raise SyllabusError("Official syllabus outline appears incomplete")
    for section in sections:
        if not section["title"] or not section["category"] or not 1 <= len(section["topics"]) <= 50:
            raise SyllabusError("Official syllabus section is incomplete")
        if any(not value or len(value) > 300 for value in [section["title"], *section["topics"]]):
            raise SyllabusError("Official syllabus heading is malformed")
    if len({s["title"] for s in sections}) != len(sections):
        raise SyllabusError("Official syllabus contains duplicate section headings")
    return {**candidates[0], "sections": sections}


def parse_download(html: str, page_url: str) -> dict:
    elements = list(Document(html).root.walk())
    links = {stable_document_url(node.attrs["data-downloadurl"], page_url)
             for node in elements if node.tag == "a" and node.attrs.get("data-downloadurl")}
    if not links:
        links = {stable_document_url(node.attrs["href"], page_url) for node in elements
                 if node.tag == "a" and urlsplit(node.attrs.get("href", "")).path.lower().endswith(".pdf")}
    if len(links) != 1:
        raise SyllabusError("Official PDF download cannot be identified unambiguously")
    updated = []
    for node in elements:
        if node.tag == "li" and "最終更新日時" in node.text():
            match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", node.text())
            if match:
                updated.append(date.fromisoformat(match[0]).isoformat())
    if len(updated) != 1:
        raise SyllabusError("Official download update date is missing")
    return {"document_url": links.pop(), "source_updated_at": updated[0]}


def collect_syllabus(*, fetcher=fetch, checked_at: str | None = None) -> dict:
    outline = parse_outline(fetcher(SOURCE_URL, HTML_LIMIT).decode("utf-8"))
    download = parse_download(fetcher(outline["download_page_url"], HTML_LIMIT).decode("utf-8"), outline["download_page_url"])
    document = fetcher(download["document_url"], PDF_LIMIT)
    if not document.startswith(b"%PDF-") or b"%%EOF" not in document[-2048:]:
        raise SyllabusError("Official download is not a complete PDF")
    data = {"schema_version": SCHEMA_VERSION, "source_url": SOURCE_URL + "#general_No03",
            **outline, **download, "document_sha256": hashlib.sha256(document).hexdigest()}
    semantic = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    data["content_hash"] = hashlib.sha256(semantic.encode()).hexdigest()
    data["checked_at"] = checked_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return data


def sync(output: Path = DEFAULT_OUTPUT, *, fetcher=fetch) -> dict:
    data = collect_syllabus(fetcher=fetcher)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Failed requests, incomplete parsing, and interrupted writes retain last good data.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".syllabus-", delete=False) as target:
            temporary = Path(target.name)
            json.dump(data, target, ensure_ascii=False, indent=2)
            target.write("\n")
        temporary.replace(output)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    data = sync(args.output)
    print(f"JDLA syllabus checked: {len(data['sections'])} sections, updated {data['source_updated_at']}, {data['content_hash']}")


if __name__ == "__main__":
    main()
