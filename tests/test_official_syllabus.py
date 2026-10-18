"""Protect current-syllabus selection and preservation of last known good data."""

import json

import pytest

from tools.sync_official_syllabus import (
    SOURCE_URL, SyllabusError, collect_syllabus, official_url, parse_download,
    parse_outline, stable_document_url, sync,
)


PAGE_URL = "https://www.jdla.org/download/current/"
PDF_URL = PAGE_URL + "?wpdmdl=123"
PDF = b"%PDF-1.7\nfixture document\n%%EOF\n"


def page(*, old_first=False, title="G検定の試験出題範囲（シラバス）G2024#6～"):
    old = '''<div class="wp-block-column"><h3>旧シラバス（G2024#5まで適用）</h3>
        <a href="/download/old/">G検定のシラバス G2024#5まで</a></div>'''
    current = f'''<div class="wp-block-column"><h3>詳細な試験範囲はこちら（G2024#6から適用）</h3>
        <h3 class="package-title"><a href="{PAGE_URL}">{title}</a></h3></div>'''
    groups = ''.join(f'<h3 class="ac-item"><strong>分野{i}</strong></h3>' +
                     ''.join(f'<h4>項目{i}-{j} &amp; 理解</h4>' for j in range(4)) for i in range(8))
    return f'''<html><h2>前の見出し</h2><h3 class="ac-item">無関係</h3>
        <h2 id="general_No03">G検定の試験範囲（シラバス）</h2><h3>【技術分野】</h3>
        {groups}{old + current if old_first else current + old}
        <h2 id="general_No06">資料</h2><h4>これは含めない</h4></html>'''


def detail(*, url=PDF_URL + "&refresh=temporary"):
    return f'''<ul><li>最終更新日時 <span>2026-05-13</span></li></ul>
        <a data-downloadurl="{url.replace('&', '&amp;')}">ダウンロード</a>'''


def fetcher(html=None, pdf=PDF):
    responses = {SOURCE_URL: (html or page()).encode(), PAGE_URL: detail().encode(), PDF_URL: pdf}
    return lambda url, limit: responses[url]


@pytest.mark.parametrize("old_first", [True, False])
def test_selects_current_syllabus_regardless_of_old_link_order(old_first):
    result = parse_outline(page(old_first=old_first))
    assert result["download_page_url"] == PAGE_URL
    assert result["revision"].endswith("G2024#6～")
    assert len(result["sections"]) == 8
    assert sum(len(section["topics"]) for section in result["sections"]) == 32
    assert result["sections"][0]["topics"][0] == "項目0-0 & 理解"


def test_missing_or_ambiguous_current_syllabus_is_rejected():
    with pytest.raises(SyllabusError, match="unambiguously"):
        parse_outline(page(title="G検定のシラバス G2024#5まで"))
    duplicate = '<a href="/download/another/">G検定のシラバス G2027#1～</a>'
    with pytest.raises(SyllabusError, match="unambiguously"):
        parse_outline(page().replace('<h2 id="general_No06">', duplicate + '<h2 id="general_No06">'))


def test_changed_page_layout_and_partial_outline_fail_safely():
    with pytest.raises(SyllabusError, match="section is missing"):
        parse_outline(page().replace('id="general_No03"', 'id="changed"'))
    with pytest.raises(SyllabusError, match="incomplete"):
        parse_outline(page().replace('class="ac-item"', 'class="changed"'))


@pytest.mark.parametrize("url", ["http://www.jdla.org/a", "https://evil.example/a", "https://www.jdla.org.evil.example/a", "https://user@www.jdla.org/a", "javascript:alert(1)", "https://www.jdla.org:443/a"])
def test_only_official_https_links_are_accepted(url):
    with pytest.raises(SyllabusError):
        official_url(url)


def test_download_url_drops_refresh_token_and_reads_visible_update_date():
    result = parse_download(detail(), PAGE_URL)
    assert result == {"document_url": PDF_URL, "source_updated_at": "2026-05-13"}
    assert stable_document_url("/files/syllabus.pdf", PAGE_URL) == "https://www.jdla.org/files/syllabus.pdf"
    with pytest.raises(SyllabusError):
        parse_download(detail(url="https://evil.example/file.pdf"), PAGE_URL)
    with pytest.raises(SyllabusError, match="date is missing"):
        parse_download(detail().replace("2026-05-13", "unknown"), PAGE_URL)


def test_hash_ignores_check_time_but_detects_pdf_only_changes():
    first = collect_syllabus(fetcher=fetcher(), checked_at="2026-10-18T00:00:00Z")
    checked_again = collect_syllabus(fetcher=fetcher(), checked_at="2026-10-19T00:00:00Z")
    changed = collect_syllabus(fetcher=fetcher(pdf=PDF.replace(b"fixture", b"revised")))
    assert first["content_hash"] == checked_again["content_hash"]
    assert first["checked_at"] != checked_again["checked_at"]
    assert first["content_hash"] != changed["content_hash"]
    assert first["document_sha256"] != changed["document_sha256"]


@pytest.mark.parametrize("bad_pdf", [b"<html>Temporary unavailable</html>", b"%PDF-1.7\ntruncated"])
def test_bad_document_keeps_last_good_snapshot(tmp_path, bad_pdf):
    output = tmp_path / "official-syllabus.json"
    output.write_text('{"previous":"valid snapshot"}', encoding="utf-8")
    with pytest.raises(SyllabusError, match="complete PDF"):
        sync(output, fetcher=fetcher(pdf=bad_pdf))
    assert output.read_text() == '{"previous":"valid snapshot"}'
    assert list(tmp_path.iterdir()) == [output]


def test_successful_sync_writes_validated_snapshot(tmp_path):
    output = tmp_path / "nested" / "official-syllabus.json"
    data = sync(output, fetcher=fetcher())
    assert json.loads(output.read_text()) == data
    assert data["schema_version"] == "gtest_official_syllabus_v1"
    assert data["source_url"] == SOURCE_URL + "#general_No03"
    assert list(output.parent.iterdir()) == [output]
