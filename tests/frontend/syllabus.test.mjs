import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { syllabusMarkup, validateSyllabus, SYLLABUS_SOURCE } from '../../frontend/src/syllabus.js';

const snapshot = JSON.parse(readFileSync(new URL('../fixtures/official-syllabus-reviewed.json', import.meta.url), 'utf8'));
const now = Date.parse(snapshot.checked_at);

test('the automatically refreshed snapshot remains displayable after an official revision', () => {
  const latest = JSON.parse(readFileSync(new URL('../../frontend/src/official-syllabus.json', import.meta.url), 'utf8'));
  validateSyllabus(latest);
  const html = syllabusMarkup(latest);
  for (const section of latest.sections) assert.ok(html.includes(section.title));
  assert.ok(html.includes(latest.document_url));
});

test('the reviewed official snapshot exposes every topic, learning point and original source', () => {
  assert.equal(validateSyllabus(snapshot), snapshot);
  const html = syllabusMarkup(snapshot, { now });
  assert.equal((html.match(/class="syllabus-section"/g) || []).length, snapshot.sections.length);
  assert.match(html, /第1.4版/);
  assert.equal((html.match(/押さえるポイント/g) || []).length, 10);
  for (const section of snapshot.sections) {
    for (const topic of section.topics) assert.ok(html.includes(topic));
  }
  assert.ok(html.includes(snapshot.document_url));
  assert.ok(html.includes(SYLLABUS_SOURCE));
  assert.match(html, /公式の配点や頻出順位を示すものではありません/);
});

test('changed official material shows the new outline without obsolete reviewed guidance', () => {
  const changed = structuredClone(snapshot);
  changed.content_hash = 'a'.repeat(64);
  changed.sections[0].topics = ['改訂された項目'];
  const html = syllabusMarkup(changed, { now });
  assert.match(html, /改訂された項目/);
  assert.match(html, /学習ポイントは確認中/);
  assert.doesNotMatch(html, /押さえるポイント|第1.4版|今回の版で変わったところ/);
});

test('offline, stale and failed refresh states preserve the actual verification date', () => {
  for (const options of [{ now: now + 49 * 3600000 }, { now, offline: true }, { now, failed: true }]) {
    const html = syllabusMarkup(snapshot, options);
    assert.match(html, /現在の内容は公式原文でもご確認ください/);
    assert.match(html, /公式確認：/);
    assert.doesNotMatch(html, /最新確認済み/);
  }
  const unavailable = syllabusMarkup(null, { failed: true });
  assert.ok(unavailable.includes(SYLLABUS_SOURCE));
  assert.match(unavailable, /読み込めませんでした/);
});

test('untrusted source links, malformed dates and incomplete outlines are rejected', () => {
  for (const change of [
    { document_url: 'javascript:alert(1)' },
    { source_url: 'https://www.jdla.org.evil.test/syllabus' },
    { download_page_url: 'https://user:pass@www.jdla.org/' },
    { checked_at: 'unknown' },
    { checked_at: '2999-01-01T00:00:00Z' },
    { content_hash: '' },
    { sections: [] },
    { sections: [{ id: 's01', title: 'title', category: 'category', topics: [] }] },
  ]) assert.throws(() => validateSyllabus({ ...snapshot, ...change }));
});

test('remote headings are escaped and external source links isolate the new tab', () => {
  const changed = structuredClone(snapshot);
  changed.content_hash = 'b'.repeat(64);
  changed.sections[0].title = '<img src=x onerror=alert(1)>';
  const html = syllabusMarkup(changed, { now });
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
  assert.equal((html.match(/target="_blank"/g) || []).length, (html.match(/rel="noopener noreferrer"/g) || []).length);
});
