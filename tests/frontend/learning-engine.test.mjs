import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createEngine } from '../../frontend/src/learning-engine.js';

const payload = JSON.parse(await readFile(new URL('../../frontend/src/question-bank.json', import.meta.url), 'utf8'));
const KEY = 'gtestQuizOfflineStateV1';
const HOUR = 3600000;
function memory(initial = null) {
  const values = new Map(initial ? [[KEY, JSON.stringify(initial)]] : []);
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
}
function fixture(source = payload, initial = null) {
  let date = new Date('2026-10-18T04:00:00.000Z');
  const storage = memory(initial);
  const now = () => new Date(date);
  const engine = createEngine(source, { storage, now, random: () => 0 });
  return { engine, storage, now, at: value => { date = new Date(value); }, advance: ms => { date = new Date(date.getTime() + ms); } };
}
const wrong = q => (q.correct_index + 1) % q.choices.length;

test('the real bank starts with unique questions, correct chapter totals and balanced sessions', () => {
  const { engine } = fixture();
  assert.ok(engine.bank.length > 500);
  assert.equal(new Set(engine.bank.map(q => q.id)).size, engine.bank.length);
  assert.equal(engine.getChapters().reduce((total, chapter) => total + chapter.total, 0), engine.bank.length);
  assert.equal(engine.getStats().unseenCount, engine.bank.length);
  const session = engine.buildSession({ limit: 10 });
  assert.equal(session.ids.length, 10);
  assert.equal(new Set(session.ids).size, 10);
  assert.equal(new Set(session.ids.map(id => engine.getQuestion(id).chapter_id)).size, 10);
  assert.deepEqual(engine.buildSession({ mode: 'review' }).ids, []);
  assert.deepEqual(engine.buildSession({ mode: 'bookmarked' }).ids, []);
});

test('answer truth comes from the bank and scheduling keeps the previous algorithm', () => {
  const { engine, now, advance } = fixture();
  const q = engine.bank.find(item => item.difficulty === 'standard');
  const result = engine.answer(q.id, q.correct_index, 1000);
  assert.equal(result.correct, true);
  assert.equal(result.correctIndex, q.correct_index);
  assert.equal(result.explanation, q.explanation);
  assert.equal(new Date(result.dueAt) - now(), 12 * HOUR);
  assert.equal(engine.state.learningItems[q.id].repetitions, 1);
  advance(HOUR);
  engine.answer(q.id, q.correct_index, 1000);
  assert.equal(engine.state.learningItems[q.id].interval_hours, 48);
  engine.answer(q.id, wrong(q), 120000);
  assert.equal(engine.state.learningItems[q.id].interval_hours, 0.5);
  assert.equal(engine.state.learningItems[q.id].repetitions, 0);
  assert.equal(engine.state.learningItems[q.id].lapses, 1);
  const stats = engine.getStats();
  assert.equal(stats.total, 3);
  assert.equal(stats.correct, 2);
  assert.equal(stats.accuracy, 2 / 3);
  assert.equal(stats.learnedCount, 1);
  assert.equal(stats.weakCount, 1);
  assert.equal(engine.getHistory()[0].chosen_index, wrong(q));
  assert.equal(engine.getReviewItems()[0].reason, 'wrong');
  assert.throws(() => engine.answer(q.id, q.choices.length, 0));
  assert.throws(() => engine.answer(q.id, -1, 0));
  assert.throws(() => engine.answer(q.id, 1.5, 0));
  assert.throws(() => engine.answer('missing', 0, 0));
  assert.throws(() => engine.answer(q.id, 0, NaN));
  assert.equal(engine.getStats().total, 3);
});

test('review is due-first, then latest wrong; a new correct attempt removes a wrong-only item', () => {
  const { engine, advance } = fixture();
  const [q1, q2, q3] = engine.bank;
  engine.answer(q1.id, wrong(q1), 120000);
  advance(HOUR);
  engine.answer(q2.id, wrong(q2), 120000);
  engine.answer(q3.id, q3.correct_index, 120000);
  const items = engine.getReviewItems();
  assert.equal(items[0].id, q1.id);
  assert.equal(items[0].reason, 'due');
  assert.equal(items[1].id, q2.id);
  assert.equal(items[1].reason, 'wrong');
  assert.equal(engine.buildSession({ limit: 10 }).ids[0], q1.id);
  assert.deepEqual(engine.buildSession({ mode: 'review' }).ids, [q1.id, q2.id]);
  engine.answer(q2.id, q2.correct_index, 1000);
  assert.deepEqual(engine.buildSession({ mode: 'review' }).ids, [q1.id]);
});

test('bookmarks, explicit ids and chapter sessions never leak other questions', () => {
  const { engine } = fixture();
  const [q1, q2] = engine.bank;
  assert.equal(engine.toggleBookmark(q1.id), true);
  assert.equal(engine.isBookmarked(q1.id), true);
  assert.deepEqual(engine.buildSession({ mode: 'bookmarked' }).ids, [q1.id]);
  assert.deepEqual(engine.buildSession({ ids: [q2.id, q1.id, q2.id, 'missing'] }).ids, [q2.id, q1.id]);
  const session = engine.buildSession({ mode: 'chapter', chapter: { id: q1.chapter_id, group: q1.chapter_group }, limit: 100 });
  assert.ok(session.ids.length > 0);
  assert.ok(session.ids.every(id => engine.getQuestion(id).chapter_id === q1.chapter_id && engine.getQuestion(id).chapter_group === q1.chapter_group));
  assert.deepEqual(engine.buildSession({ mode: 'chapter', chapter: 'missing' }).ids, []);
  assert.equal(engine.toggleBookmark(q1.id), false);
  assert.equal(engine.getStats().bookmarkCount, 0);
});

test('serializable session and additive state survive a reload using the legacy storage key', () => {
  const { engine, storage, now } = fixture();
  const session = engine.buildSession({ limit: 3 });
  const q = engine.getQuestion(session.ids[0]);
  const result = engine.answer(q.id, q.correct_index, 500);
  session.answers.push({ questionId: q.id, choiceIndex: q.correct_index, correct: result.correct, dueAt: result.dueAt, elapsedMs: 500 });
  session.index = 1;
  engine.saveSession(session);
  engine.setDailyGoal(20);
  engine.toggleBookmark(q.id);
  const reloaded = createEngine(payload, { storage, now });
  assert.deepEqual(reloaded.getSession(), session);
  assert.equal(reloaded.getStats().total, 1);
  assert.equal(reloaded.getStats().dailyGoal, 20);
  assert.equal(reloaded.isBookmarked(q.id), true);
  const detached = reloaded.getSession();
  detached.index = 99;
  assert.equal(reloaded.getSession().index, 1);
  reloaded.clearSession();
  assert.equal(createEngine(payload, { storage, now }).getSession(), null);
});

test('Tokyo midnight, daily streak grace and the seven-day chart use the learning day', () => {
  const { engine, at } = fixture();
  const q = engine.bank[0];
  at('2026-10-17T14:59:00Z'); // 17th 23:59 JST
  engine.answer(q.id, q.correct_index, 1000);
  at('2026-10-17T15:01:00Z'); // 18th 00:01 JST
  engine.answer(q.id, wrong(q), 1000);
  assert.equal(engine.getStats().todayCount, 1);
  assert.equal(engine.getStats().todayCorrect, 0);
  assert.equal(engine.getStats().streak, 2);
  assert.equal(engine.getStats().week.at(-1).date, '2026-10-18');
  assert.equal(engine.getStats().week.at(-1).count, 1);
  assert.equal(engine.getStats().week.at(-2).count, 1);
  at('2026-10-18T15:01:00Z'); // 19th, today not started
  assert.equal(engine.getStats().streak, 2);
  at('2026-10-19T15:01:00Z'); // 20th, one missed day
  assert.equal(engine.getStats().streak, 0);
});

test('bank-version changes preserve history; retired ids stay in export but not active statistics', () => {
  const original = fixture();
  const [q1, q2] = original.engine.bank;
  original.engine.answer(q1.id, q1.correct_index, 1000);
  original.engine.answer(q2.id, q2.correct_index, 1000);
  original.engine.toggleBookmark(q2.id);
  const changed = { meta: { ...payload.meta, bank_version: 'next-version' }, questions: [q1] };
  const engine = createEngine(changed, { storage: original.storage, now: original.now });
  assert.equal(engine.state.answers.length, 2);
  assert.equal(engine.getStats().total, 1);
  assert.equal(engine.getStats().bookmarkCount, 0);
  assert.equal(engine.exportData().state.answers.length, 2);
  assert.equal(engine.state.bankVersion, 'next-version');
});

test('legacy exports import, regrade falsified correctness and rebuild schedules from answer truth', () => {
  const { engine } = fixture();
  const q = engine.bank[0];
  const answer = { question_id: q.id, selected_index: wrong(q), correct: true, elapsed_ms: 120000, answered_at: '2026-10-17T10:00:00Z' };
  engine.importData({ schema_version: 'gtest_quiz_offline_export_v1', bank_version: 'old', state: { answers: [answer], learningItems: {} } });
  assert.equal(engine.getStats().correct, 0);
  assert.equal(engine.state.learningItems[q.id].interval_hours, 0.5);
  assert.equal(engine.getHistory()[0].correct, false);
  engine.importData({ answers: [{ ...answer, selected_index: q.correct_index, correct: false }], learning_items: [] });
  assert.equal(engine.getStats().correct, 1);
  assert.equal(engine.state.learningItems[q.id].interval_hours, 12);
  const exported = engine.exportData();
  engine.importData(JSON.stringify(exported));
  assert.equal(engine.getStats().total, 1);
  assert.equal(engine.getHistory()[0].chosen_index, q.correct_index);
});

test('invalid imports are atomic and reject schema, selection, size and prototype hazards', () => {
  const { engine } = fixture();
  const q = engine.bank[0];
  engine.answer(q.id, q.correct_index, 1000);
  const before = JSON.stringify(engine.state);
  const badImports = [
    '{bad', { schema_version: 'other', answers: [] }, { state: null }, { answer: [] },
    { answers: [{ question_id: q.id, selected_index: 99, answered_at: '2026-10-18T00:00:00Z' }] },
    { answers: [{ question_id: q.id, selected_index: 0, answered_at: 'invalid' }] },
    JSON.parse('{"answers":[],"__proto__":{"polluted":true}}'),
    { answers: [], bookmarks: ['__proto__'] },
    { answers: [], learning_items: [{ question_id: '__proto__' }] },
    { answers: [], dailyGoal: -1 },
    { answers: [], padding: 'x'.repeat(10 * 1024 * 1024) },
  ];
  for (const data of badImports) {
    assert.throws(() => engine.importData(data));
    assert.equal(JSON.stringify(engine.state), before);
  }
  assert.equal({}.polluted, undefined);
});

test('storage failures remain visible while answers and sessions still work in memory', () => {
  let broken = true;
  const storage = { getItem: () => null, setItem: () => { if (broken) throw new Error('Quota exceeded'); } };
  const engine = createEngine(payload, { storage, now: () => new Date('2026-10-18T04:00:00Z') });
  const q = engine.bank[0];
  assert.doesNotThrow(() => engine.answer(q.id, q.correct_index, 500));
  assert.equal(engine.getStats().total, 1);
  assert.match(engine.persistenceError, /Quota exceeded/);
  assert.equal(engine.getStats().storageError, engine.persistenceError);
  engine.saveSession(engine.buildSession());
  assert.ok(engine.getSession());
  broken = false;
  engine.setDailyGoal(15);
  assert.equal(engine.persistenceError, null);
});

test('empty banks remain usable and suspended-tab elapsed time cannot block an answer', () => {
  const empty = fixture({ meta: {}, questions: [] }).engine;
  assert.deepEqual(empty.buildSession().ids, []);
  assert.deepEqual(empty.getChapters(), []);
  assert.equal(empty.getStats().accuracy, 0);
  assert.equal(empty.getStats().week.length, 7);
  const { engine } = fixture();
  const q = engine.bank[0];
  engine.answer(q.id, q.correct_index, 24 * HOUR);
  assert.equal(engine.getHistory()[0].elapsed_ms, HOUR);
  assert.doesNotThrow(() => engine.importData(engine.exportData()));
});

test('identically named chapters remain separated by their group', () => {
  const q = payload.questions[0];
  const source = { meta: {}, questions: [
    { ...q, id: 'group-a', chapter_group: 'A', chapter_id: 'Same chapter' },
    { ...q, id: 'group-b', chapter_group: 'B', chapter_id: 'Same chapter' },
  ] };
  const { engine } = fixture(source);
  assert.equal(engine.getChapters().length, 2);
  assert.deepEqual(engine.buildSession({ mode: 'chapter', chapter: { id: 'Same chapter', group: 'A' } }).ids, ['group-a']);
  assert.deepEqual(engine.buildSession({ mode: 'chapter', chapter: engine.getChapters()[1].key }).ids, ['group-b']);
});

test('holding 422 questions preserves their legacy history through load, save, export, import and reactivation', () => {
  const original = fixture();
  const activeQuestion = original.engine.bank[0];
  const heldQuestions = original.engine.bank.slice(-422);
  const heldIds = new Set(heldQuestions.map(q => q.id));
  for (const q of heldQuestions) original.engine.answer(q.id, q.correct_index, 1000);
  original.engine.answer(activeQuestion.id, wrong(activeQuestion), 1000);
  original.engine.toggleBookmark(heldQuestions[0].id);
  const legacyState = JSON.parse(original.storage.getItem(KEY));
  // Match the original application's persisted answer shape (selected_index only).
  legacyState.answers.forEach(row => delete row.chosen_index);
  original.storage.setItem(KEY, JSON.stringify(legacyState));
  const activePayload = { meta: payload.meta, questions: payload.questions.filter(q => !heldIds.has(q.id)) };
  assert.equal(activePayload.questions.length, payload.questions.length - 422);
  const reduced = createEngine(activePayload, { storage: original.storage, now: original.now });
  assert.equal(reduced.state.answers.length, 423);
  assert.equal(reduced.getStats().total, 1);
  assert.equal(reduced.getStats().correct, 0);
  assert.equal(reduced.getStats().bookmarkCount, 0);
  assert.equal(reduced.getHistory().length, 1);
  assert.ok(reduced.buildSession({ limit: 10000 }).ids.every(id => !heldIds.has(id)));
  assert.ok(reduced.getReviewItems().every(q => !heldIds.has(q.id)));
  reduced.setDailyGoal(25); // Ordinary persistence must not erase inactive history.
  const exported = reduced.exportData();
  assert.equal(exported.state.answers.filter(row => heldIds.has(row.question_id)).length, 422);
  assert.ok(exported.state.bookmarks.includes(heldQuestions[0].id));
  const importedStorage = memory();
  const imported = createEngine(activePayload, { storage: importedStorage, now: original.now });
  assert.equal(imported.importData(JSON.stringify(exported)).retainedInactive, 422);
  assert.equal(imported.getStats().total, 1);
  assert.equal(imported.exportData().state.answers.length, 423);
  const restored = createEngine(payload, { storage: importedStorage, now: original.now });
  assert.equal(restored.getStats().total, 423);
  assert.equal(restored.getStats().correct, 422);
  assert.equal(restored.getStats().bookmarkCount, 1);
  assert.equal(restored.getHistory().length, 423);
  assert.equal(restored.getStats().dailyGoal, 25);
});

test('the answered current slot survives persistence without advancing or recording it again', () => {
  const { engine, storage, now } = fixture();
  const session = engine.buildSession({ limit: 3 });
  const q = engine.getQuestion(session.ids[0]);
  const choiceIndex = wrong(q), elapsedMs = 12000;
  const result = engine.answer(q.id, choiceIndex, elapsedMs);
  session.answers.push({ questionId: q.id, choiceIndex, correct: result.correct, dueAt: result.dueAt, elapsedMs });
  assert.equal(session.index, 0); // Explanation screen remains on this question until Next.
  engine.saveSession(session);
  const firstLoad = createEngine(payload, { storage, now });
  const restored = firstLoad.getSession();
  assert.equal(restored.index, 0);
  assert.equal(restored.ids[restored.index], q.id);
  assert.deepEqual(restored.answers[restored.index], session.answers[0]);
  assert.equal(restored.answers[0].choiceIndex, choiceIndex);
  assert.equal(firstLoad.getStats().total, 1);
  firstLoad.saveSession(restored);
  const secondLoad = createEngine(payload, { storage, now });
  assert.equal(secondLoad.getStats().total, 1);
  assert.equal(secondLoad.getSession().answers.length, 1);
  assert.equal(secondLoad.getSession().answers[0].choiceIndex, choiceIndex);
  assert.equal(secondLoad.state.learningItems[q.id].lapses, 1);
  const next = secondLoad.getSession();
  next.index += 1;
  secondLoad.saveSession(next);
  assert.equal(createEngine(payload, { storage, now }).getSession().index, 1);
  assert.equal(secondLoad.getStats().total, 1);
});

test('session imports regrade forged correctness, clamp time and drop unknown record fields', () => {
  const { engine } = fixture();
  const session = engine.buildSession({ limit: 2 });
  const q = engine.getQuestion(session.ids[0]);
  session.answers = [{ questionId: q.id, choiceIndex: wrong(q), correct: true, dueAt: null, elapsedMs: 24 * HOUR, extra: 'discard' }];
  engine.importData({ schema_version: 'gtest_quiz_offline_export_v1', state: { answers: [], session } });
  assert.deepEqual(engine.getSession().answers[0], { questionId: q.id, choiceIndex: wrong(q), correct: false, dueAt: null, elapsedMs: HOUR });
  assert.equal(engine.getSession().index, 0);
  assert.equal(engine.getStats().total, 0);
});

test('invalid session imports reject atomically instead of skipping unanswered questions', () => {
  const { engine } = fixture();
  const session = engine.buildSession({ limit: 3 });
  const q = engine.getQuestion(session.ids[0]);
  const record = { questionId: q.id, choiceIndex: q.correct_index, correct: true, dueAt: null, elapsedMs: 500 };
  engine.saveSession(session);
  engine.answer(q.id, q.correct_index, 500);
  const before = JSON.stringify(engine.state);
  const invalid = [
    { ...session, answers: [{ ...record, choiceIndex: q.choices.length }] },
    { ...session, answers: [{ ...record, choiceIndex: -1 }] },
    { ...session, answers: [{ ...record, questionId: 'unknown' }] },
    { ...session, answers: [{ ...record, questionId: session.ids[1] }] },
    { ...session, answers: [{ ...record, dueAt: 'not a date' }] },
    { ...session, answers: [{ ...record, elapsedMs: '1000' }] },
    { ...session, index: 2, answers: [record] },
    { ...session, index: 1, answers: [] },
    { ...session, answers: [record, { ...record, questionId: session.ids[1] }] },
    { ...session, answers: [record, { ...record, choiceIndex: wrong(q) }] },
  ];
  for (const invalidSession of invalid) {
    assert.throws(() => engine.importData({ answers: [], session: invalidSession }));
    assert.equal(JSON.stringify(engine.state), before);
  }
});

test('matching duplicate session records collapse to one and retain the answered prefix', () => {
  const { engine } = fixture();
  const session = engine.buildSession({ limit: 1 });
  const q = engine.getQuestion(session.ids[0]);
  const record = { questionId: q.id, choiceIndex: q.correct_index, correct: false, dueAt: null, elapsedMs: 500 };
  session.answers = [record, { ...record }];
  engine.saveSession(session);
  assert.equal(engine.getSession().answers.length, 1);
  assert.equal(engine.getSession().answers[0].correct, true);
  assert.equal(engine.getSession().index, 0);
});

test('indexed search preserves case-insensitive matching across text, chapters and choices', () => {
  const { engine } = fixture();
  for (const query of ['AI', ' ニューラルネットワーク ', '法律・倫理・社会', 'zzzz_NO_MATCH', '']) {
    const normalized = query.trim().toLocaleLowerCase('ja');
    const expected = engine.bank.filter(q => `${q.question} ${q.chapter_id} ${q.chapter_group} ${q.choices.join(' ')}`.toLocaleLowerCase('ja').includes(normalized));
    assert.deepEqual(engine.searchQuestions(query), expected);
  }
  assert.equal(engine.chapterCount, engine.getChapters().length);
});

test('current question saves history and feedback atomically with one write, rejects repeat submission', () => {
  const { storage, now } = fixture();
  const writes = [];
  const trackedStorage = { getItem: storage.getItem, setItem: (key, value) => { writes.push(JSON.parse(value)); storage.setItem(key, value); } };
  const engine = createEngine(payload, { storage: trackedStorage, now, random: () => 0 });
  engine.saveSession(engine.buildSession({ limit: 2 }));
  writes.length = 0;
  const q = engine.getQuestion(engine.getSession().ids[0]);
  const session = engine.answerCurrentQuestion(wrong(q), 120000);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].answers.length, 1);
  assert.equal(writes[0].session.answers.length, 1);
  assert.equal(session.answers[0].correct, false);
  assert.equal(session.index, 0);
  const restored = createEngine(payload, { storage, now });
  assert.deepEqual(restored.getSession(), session);
  assert.equal(restored.getStats().total, 1);
  assert.throws(() => engine.answerCurrentQuestion(q.correct_index, 500));
  assert.equal(engine.getStats().total, 1);
  assert.equal(writes.length, 1);
  session.index += 1;
  engine.saveSession(session);
  const next = engine.getQuestion(session.ids[1]);
  engine.answerCurrentQuestion(next.correct_index, HOUR * 2);
  assert.equal(engine.getStats().total, 2);
  assert.equal(engine.getStats().correct, 1);
  assert.equal(engine.getSession().answers[1].elapsedMs, HOUR);
});

test('invalid session answers leave persisted history and progress unchanged', () => {
  const { engine, storage } = fixture();
  assert.throws(() => engine.answerCurrentQuestion(0));
  engine.saveSession(engine.buildSession({ limit: 1 }));
  const before = storage.getItem(KEY);
  for (const [choice, elapsed] of [[-1, 0], [99, 0], [1.5, 0], [0, NaN], [0, -1]]) {
    assert.throws(() => engine.answerCurrentQuestion(choice, elapsed));
    assert.equal(storage.getItem(KEY), before);
    assert.equal(engine.getStats().total, 0);
    assert.equal(engine.getSession().answers.length, 0);
  }
});
