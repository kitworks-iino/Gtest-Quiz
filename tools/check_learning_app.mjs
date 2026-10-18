/** Exercise the shipped learning engine and content guard, without network or user data. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createEngine } from '../frontend/src/learning-engine.js';
import { partitionQuestions } from '../frontend/src/content-guard.js';

const readJson = async (path) => JSON.parse(await readFile(new URL(path, import.meta.url), 'utf8'));
const payload = await readJson('../frontend/src/question-bank.json');
const holds = await readJson('../frontend/src/question-review-holds.json');
const partition = partitionQuestions(payload, holds);
const activePayload = { ...payload, questions: partition.questions };
const values = new Map();
const storage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
const now = () => new Date('2026-10-18T04:00:00Z');
const engine = createEngine(activePayload, { storage, now, random: () => 0 });
const session = engine.buildSession({ limit: 10 });
assert.ok(session.ids.length > 0, 'At least one usable question is required.');
assert.equal(new Set(session.ids).size, session.ids.length);

const question = engine.getQuestion(session.ids[0]);
const wrongIndex = (question.correct_index + 1) % question.choices.length;
const answer = engine.answer(question.id, wrongIndex, 30000);
assert.equal(answer.correct, false);
assert.equal(answer.explanation, question.explanation);
assert.equal(answer.correctIndex, question.correct_index);
assert.ok(engine.buildSession({ mode: 'review' }).ids.includes(question.id));
session.answers.push({ questionId: question.id, choiceIndex: wrongIndex, correct: false,
  dueAt: answer.dueAt, elapsedMs: 30000 });
session.index = 0; // Reloading an answered question must not grade it again.
engine.saveSession(session);
engine.toggleBookmark(question.id);
engine.setDailyGoal(20);

const reloaded = createEngine(activePayload, { storage, now });
assert.deepEqual(reloaded.getSession(), session);
assert.equal(reloaded.getStats().total, 1);
assert.equal(reloaded.getStats().dailyGoal, 20);
assert.equal(reloaded.isBookmarked(question.id), true);
const restored = createEngine(activePayload, { storage: null, now });
restored.importData(JSON.stringify(reloaded.exportData()));
assert.deepEqual(restored.getHistory(), reloaded.getHistory());
assert.equal(restored.getStats().correct, 0);
assert.equal(restored.getStats().bookmarkCount, 1);
assert.ok(restored.getReviewItems().some((item) => item.id === question.id));

const chapters = new Map();
for (const q of partition.questions) {
  const chapter = chapters.get(q.chapter_id) ?? { id: q.chapter_id, total: 0,
    difficulty: { basic: 0, standard: 0, advanced: 0 } };
  chapter.total += 1;
  chapter.difficulty[q.difficulty] = (chapter.difficulty[q.difficulty] ?? 0) + 1;
  chapters.set(q.chapter_id, chapter);
}
console.log(JSON.stringify({ passed: true, sourceCount: partition.sourceCount,
  heldCount: partition.heldCount, availableCount: partition.questions.length,
  chapters: [...chapters.values()],
  verified: ['grading', 'explanation', 'wrong-review', 'session-resume', 'persistence',
    'bookmarks', 'daily-goal', 'export-import'] }));
