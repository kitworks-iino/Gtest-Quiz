import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { partitionQuestions } from '../../frontend/src/content-guard.js';

const bank = JSON.parse(await readFile(new URL('../../frontend/src/question-bank.json', import.meta.url), 'utf8'));
const holds = JSON.parse(await readFile(new URL('../../frontend/src/question-review-holds.json', import.meta.url), 'utf8'));
const emptyHolds = { ...holds, ids: [] };
const question = (id, explanation) => ({ id, explanation });

test('the growing bank retains usable chapters, excludes held ids and stays untouched', () => {
  const original = JSON.stringify(bank);
  const result = partitionQuestions(bank, holds);
  assert.equal(result.sourceCount, bank.questions.length);
  assert.ok(result.questions.length > 0);
  assert.equal(result.questions.length + result.heldCount, bank.questions.length);
  assert.equal(new Set(result.questions.map(row => row.chapter_id)).size, 55);
  assert.ok(result.questions.every(row => !holds.ids.includes(row.id)));
  assert.equal(JSON.stringify(bank), original);
});

test('new explicit numbered, lettered and ordinal choice references are held', () => {
  const examples = [
    '選択肢2は誤りです。', '選択肢（Ａ）は誤りです。',
    '選択肢①は誤りです。', 'インデックス0が正解です。',
    '誤答肢3は誤りです。', '最初の選択肢は誤りです。',
    '2つ目の選択肢は誤りです。', '選択肢の1番目が正解です。',
    '他の選択肢について、1つ目は教師あり学習です。',
    '誤答について、B・Cは誤りです。',
  ];
  const result = partitionQuestions({ questions: examples.map((value, i) => question(`new-${i}`, value)) }, emptyHolds);
  assert.equal(result.questions.length, 0);
  assert.equal(result.heldCount, examples.length);
});

test('law citations, math, quantities and technical labels are not mistaken for options', () => {
  const examples = [
    '著作権法第30条の4は適用されます。',
    '不正解の理由は著作権法第30条の4が適用されないことです。',
    '他の選択肢について、第30条の4は情報解析に適用されます。',
    '計算量はO(1)であり、A*探索と区別します。',
    '定数Cは探索の重みです。',
    '他の選択肢について、グループ数1はLNと等価です。',
    '入力ユニット数に1を足し、2を掛けます。',
    '選択肢2つを比較します。', '選択肢AIというラベルはありません。',
    'インデックス0は配列の先頭を表します。',
  ];
  const result = partitionQuestions({ questions: examples.map((value, i) => question(`valid-${i}`, value)) }, emptyHolds);
  assert.equal(result.questions.length, examples.length);
  assert.equal(result.heldCount, 0);
});

test('append-only updates keep historical holds and inspect new wording despite a new bank hash', () => {
  const safe = question('new-safe', '誤差逆伝播法は勾配を効率よく求めます。');
  const unsafe = question('new-needs-review', '選択肢3は誤りです。');
  const updated = { ...bank, meta: { ...bank.meta, content_hash: 'new-daily-bank-hash' }, questions: [...bank.questions, safe, unsafe] };
  const before = partitionQuestions(bank, holds);
  const result = partitionQuestions(updated, holds);
  assert.equal(result.sourceCount, before.sourceCount + 2);
  assert.equal(result.heldCount, before.heldCount + 1);
  assert.equal(result.questions.length, before.questions.length + 1);
  assert.ok(result.questions.includes(safe));
  assert.ok(!result.questions.includes(unsafe));
});

test('a missing or malformed holds document cannot silently expose historical held questions', () => {
  assert.throws(() => partitionQuestions(bank, null), TypeError);
  assert.throws(() => partitionQuestions(bank, { ...holds, ids: [null] }), TypeError);
  assert.throws(() => partitionQuestions({}, holds), TypeError);
});
