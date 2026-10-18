/** Offline learning state, scheduling and sessions. No DOM or network access. */
const STORAGE_KEY = 'gtestQuizOfflineStateV1';
const EXPORT_SCHEMA = 'gtest_quiz_offline_export_v1';
const MAX_IMPORT_BYTES = 10 * 1024 * 1024;
const MAX_ANSWERS = 100000;
const HOUR = 3600000;
const DAY = 24 * HOUR;
const DIFFICULTY = { basic: 0.25, standard: 0.55, advanced: 0.85 };
const EXPECTED_MS = { basic: 22000, standard: 36000, advanced: 52000 };
const MODES = new Set(['daily', 'review', 'bookmarked', 'chapter']);
const FORBIDDEN_KEYS = new Set(['__proto__', 'prototype', 'constructor']);
const clamp = (n, min, max) => Math.max(min, Math.min(max, n));
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const plain = (value) =>
  value !== null &&
  typeof value === 'object' &&
  (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
const clone = (value) => JSON.parse(JSON.stringify(value));
const chapterKey = (q) => `${q.chapter_group || ''}\u0000${q.chapter_id}`;

function asDate(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  let text = value.trim().replace(' ', 'T');
  // Old database exports use UTC timestamps without an explicit timezone.
  if (/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?$/.test(text)) text += 'Z';
  const date = new Date(text);
  return Number.isFinite(date.getTime()) ? date : null;
}

function dayKey(date) {
  return new Date(date.getTime() + 9 * HOUR).toISOString().slice(0, 10);
}

function validId(value) {
  return (
    typeof value === 'string' &&
    value.length > 0 &&
    value.length <= 300 &&
    !FORBIDDEN_KEYS.has(value) &&
    !/[\u0000-\u001f]/.test(value)
  );
}

function assertSafeJson(value, depth = 0, seen = new Set()) {
  if (depth > 30) throw new Error('インポートデータの階層が深すぎます。');
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number' && Number.isFinite(value)) return;
  if ((!Array.isArray(value) && !plain(value)) || seen.has(value)) {
    throw new Error('有効なJSONデータではありません。');
  }
  seen.add(value);
  for (const key of Object.keys(value)) {
    if (FORBIDDEN_KEYS.has(key)) throw new Error('安全でないプロパティが含まれています。');
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor || !own(descriptor, 'value'))
      throw new Error('JSON以外のプロパティは読み込めません。');
    assertSafeJson(descriptor.value, depth + 1, seen);
  }
  seen.delete(value);
}

function readJson(input) {
  if (typeof input === 'string' && new TextEncoder().encode(input).length > MAX_IMPORT_BYTES) {
    throw new Error('インポートファイルは10MB以下にしてください。');
  }
  let data;
  try {
    data = typeof input === 'string' ? JSON.parse(input) : input;
  } catch {
    throw new Error('JSONファイルを読み込めません。');
  }
  assertSafeJson(data);
  if (new TextEncoder().encode(JSON.stringify(data)).length > MAX_IMPORT_BYTES) {
    throw new Error('インポートファイルは10MB以下にしてください。');
  }
  return data;
}

function responseTimeScore(elapsedMs, difficulty) {
  if (!elapsedMs || elapsedMs <= 0) return 0.55;
  const ratio = clamp(elapsedMs / (EXPECTED_MS[difficulty] ?? EXPECTED_MS.standard), 0.15, 3);
  return clamp(1.2 - ratio * 0.35, 0, 1);
}

// Preserves the previous offline-app.js scheduling formula and data fields.
function updateSchedule(previous, correct, elapsedMs, difficulty, date) {
  const speed = responseTimeScore(elapsedMs, difficulty);
  const grade = correct ? (speed >= 0.82 ? 5 : speed >= 0.55 ? 4 : 3) : speed >= 0.8 ? 2 : 1;
  let easiness = Number(previous?.easiness ?? 2.3);
  let interval = Number(previous?.interval_hours ?? 0);
  let repetitions = Number(previous?.repetitions ?? 0);
  let lapses = Number(previous?.lapses ?? 0);
  if (grade < 3) {
    lapses += 1;
    repetitions = 0;
    easiness = Math.max(1.3, easiness - 0.22);
    interval = grade === 2 ? 2 : 0.5;
  } else {
    repetitions += 1;
    easiness = clamp(easiness + 0.08 * (grade - 3) + (speed - 0.55) * 0.18, 1.3, 2.85);
    if (repetitions === 1) interval = 12;
    else if (repetitions === 2) interval = 48;
    else
      interval = Math.max(
        24,
        (interval * easiness) / (1 + (DIFFICULTY[difficulty] ?? DIFFICULTY.standard) * 0.18),
      );
  }
  return {
    easiness: Number(easiness.toFixed(4)),
    interval_hours: Number(interval.toFixed(2)),
    due_at: new Date(date.getTime() + interval * HOUR).toISOString(),
    repetitions,
    lapses,
    last_grade: grade,
    retention: 1,
    updated_at: date.toISOString(),
  };
}

function wilson(correct, total) {
  if (!total) return 0;
  const z = 1.28,
    p = correct / total;
  return clamp(
    (p + (z * z) / (2 * total) - z * Math.sqrt((p * (1 - p) + (z * z) / (4 * total)) / total)) /
      (1 + (z * z) / total),
    0,
    1,
  );
}

function emptyState(version) {
  return {
    bankVersion: version,
    bankResetNoticeSeen: true,
    policyVariant: 'adaptive_mastery_v2',
    answers: [],
    learningItems: {},
    recent: [],
    bookmarks: [],
    dailyGoal: 10,
    session: null,
  };
}

export function createEngine(payload, options = {}) {
  const now = options.now ?? (() => new Date());
  const random = options.random ?? Math.random;
  let storage = null;
  let persistenceError = null;
  try {
    storage = own(options, 'storage') ? options.storage : globalThis.localStorage;
    if (storage === undefined) persistenceError = 'この環境では学習履歴を端末に保存できません。';
  } catch (error) {
    persistenceError = String(error.message || error);
  }
  const inputBank = Array.isArray(payload) ? payload : payload?.questions;
  if (!Array.isArray(inputBank)) throw new Error('問題バンクの形式が正しくありません。');
  const meta = clone(!Array.isArray(payload) && plain(payload.meta) ? payload.meta : {});
  const byId = new Map();
  const bank = inputBank.map((raw) => {
    if (
      !plain(raw) ||
      !validId(raw.id) ||
      byId.has(raw.id) ||
      typeof raw.question !== 'string' ||
      !Array.isArray(raw.choices) ||
      raw.choices.length < 2 ||
      raw.choices.length > 10 ||
      raw.choices.some((choice) => typeof choice !== 'string') ||
      !Number.isInteger(raw.correct_index) ||
      raw.correct_index < 0 ||
      raw.correct_index >= raw.choices.length
    ) {
      throw new Error('問題バンクに不正な問題または重複IDがあります。');
    }
    const q = Object.freeze({
      ...raw,
      choices: Object.freeze([...raw.choices]),
      chapter_id: String(raw.chapter_id || '未分類'),
      chapter_group: String(raw.chapter_group || ''),
      explanation: String(raw.explanation || ''),
      difficulty: own(DIFFICULTY, raw.difficulty) ? raw.difficulty : 'standard',
    });
    byId.set(q.id, q);
    return q;
  });
  Object.freeze(bank);
  // The question bank is immutable. Build searchable text once, not on every keystroke.
  const searchIndex = bank.map((question) => ({
    question,
    text: `${question.question} ${question.chapter_id} ${question.chapter_group} ${question.choices.join(' ')}`.toLocaleLowerCase(
      'ja',
    ),
  }));
  const chapterCount = new Set(bank.map(chapterKey)).size;
  const version = String(meta.bank_version || '');
  let state = emptyState(version);

  function clock() {
    const value = now();
    const date = value instanceof Date ? new Date(value.getTime()) : new Date(value);
    if (!Number.isFinite(date.getTime())) throw new Error('現在日時を取得できません。');
    return date;
  }

  function persist() {
    if (storage === null) return;
    try {
      if (!storage || typeof storage.setItem !== 'function')
        throw new Error('端末保存を利用できません。');
      storage.setItem(STORAGE_KEY, JSON.stringify(state));
      persistenceError = null;
    } catch (error) {
      persistenceError = `学習履歴を保存できませんでした: ${error.message || error}`;
    }
  }

  function normalizeAnswer(row, strict) {
    const fail = () => {
      if (strict) throw new Error('回答履歴の問題ID・選択肢・日時が不正です。');
      return null;
    };
    if (!plain(row) || !validId(row.question_id)) return fail();
    const q = byId.get(row.question_id);
    const index = row.selected_index ?? row.chosen_index;
    const date = asDate(row.answered_at);
    const elapsed = row.elapsed_ms ?? 0;
    if (
      !Number.isInteger(index) ||
      index < 0 ||
      index >= (q?.choices.length ?? 10) ||
      !date ||
      typeof elapsed !== 'number' ||
      !Number.isFinite(elapsed) ||
      elapsed < 0 ||
      elapsed > HOUR
    )
      return fail();
    return {
      ...row,
      question_id: row.question_id,
      chapter_id: q?.chapter_id || String(row.chapter_id || ''),
      selected_index: index,
      chosen_index: index,
      correct: q ? index === q.correct_index : Boolean(row.correct),
      elapsed_ms: Math.round(elapsed),
      answered_at: date.toISOString(),
    };
  }

  function normalizeSession(raw, strict = false) {
    if (raw == null) return null;
    const fail = () => {
      if (strict) throw new Error('学習セッションの形式が正しくありません。');
      return null;
    };
    if (
      !plain(raw) ||
      !MODES.has(raw.mode) ||
      !Array.isArray(raw.ids) ||
      raw.ids.length > 10000 ||
      raw.ids.some((id) => !validId(id)) ||
      new Set(raw.ids).size !== raw.ids.length ||
      !Number.isInteger(raw.index) ||
      raw.index < 0 ||
      raw.index > raw.ids.length ||
      !Array.isArray(raw.answers) ||
      raw.answers.length > 10000 ||
      !asDate(raw.startedAt)
    )
      return fail();
    const positions = new Map(raw.ids.map((id, index) => [id, index]));
    const answered = new Map();
    for (const record of raw.answers) {
      if (!plain(record) || !validId(record.questionId) || !positions.has(record.questionId))
        return fail();
      const q = byId.get(record.questionId);
      if (
        !Number.isInteger(record.choiceIndex) ||
        record.choiceIndex < 0 ||
        record.choiceIndex >= (q?.choices.length ?? 10) ||
        typeof record.elapsedMs !== 'number' ||
        !Number.isFinite(record.elapsedMs)
      )
        return fail();
      const due = record.dueAt == null ? null : asDate(record.dueAt);
      if (record.dueAt != null && !due) return fail();
      const normalized = {
        questionId: record.questionId,
        choiceIndex: record.choiceIndex,
        correct: q ? record.choiceIndex === q.correct_index : Boolean(record.correct),
        dueAt: due?.toISOString() ?? null,
        elapsedMs: Math.round(clamp(record.elapsedMs, 0, HOUR)),
      };
      const previous = answered.get(record.questionId);
      // Duplicate saved events may be collapsed, but conflicting choices are not recoverable safely.
      if (previous && previous.choiceIndex !== normalized.choiceIndex) return fail();
      if (!previous) answered.set(record.questionId, normalized);
    }
    const answerCount = answered.size;
    // Completed questions form a prefix. The currently displayed question may also be answered,
    // because its explanation stays visible until the learner explicitly presses Next.
    if (
      answerCount < raw.index ||
      answerCount > Math.min(raw.index + 1, raw.ids.length) ||
      raw.ids.slice(0, answerCount).some((id) => !answered.has(id))
    )
      return fail();
    const ids = raw.ids.filter((id) => byId.has(id));
    if (!ids.length) return null;
    return {
      mode: raw.mode,
      ids,
      index: raw.ids.slice(0, raw.index).filter((id) => byId.has(id)).length,
      answers: raw.ids
        .slice(0, answerCount)
        .filter((id) => byId.has(id))
        .map((id) => answered.get(id)),
      startedAt: asDate(raw.startedAt).toISOString(),
      ...(raw.chapter != null ? { chapter: clone(raw.chapter) } : {}),
    };
  }

  function normalizeState(source, strict = false, recompute = false) {
    if (!plain(source)) throw new Error('学習データの形式が正しくありません。');
    if (
      source.answers !== undefined &&
      (!Array.isArray(source.answers) || source.answers.length > MAX_ANSWERS)
    ) {
      throw new Error('回答履歴の形式または件数が正しくありません。');
    }
    const result = emptyState(version);
    result.bankVersion = version || String(source.bankVersion || '');
    result.bankResetNoticeSeen = source.bankResetNoticeSeen !== false;
    const policies = ['adaptive_mastery_v2', 'chapter_balanced_v1', 'random_baseline_v1'];
    result.policyVariant = policies.includes(source.policyVariant)
      ? source.policyVariant
      : policies[0];
    const regraded = new Set();
    result.answers = (source.answers || [])
      .map((row) => {
        const normalized = normalizeAnswer(row, strict);
        if (normalized && normalized.correct !== row.correct) regraded.add(normalized.question_id);
        return normalized;
      })
      .filter(Boolean)
      .sort((a, b) => a.answered_at.localeCompare(b.answered_at));
    if (
      source.bookmarks !== undefined &&
      (!Array.isArray(source.bookmarks) || source.bookmarks.some((id) => !validId(id)))
    ) {
      if (strict) throw new Error('ブックマークの問題IDが不正です。');
    } else result.bookmarks = [...new Set(source.bookmarks || [])];
    if (
      source.dailyGoal !== undefined &&
      (!Number.isInteger(source.dailyGoal) || source.dailyGoal < 1 || source.dailyGoal > 200)
    ) {
      if (strict) throw new Error('1日の目標は1〜200問にしてください。');
    } else result.dailyGoal = source.dailyGoal ?? 10;
    let oldItems = source.learningItems ?? source.learning_items ?? {};
    if (Array.isArray(oldItems)) {
      if (strict && oldItems.some((item) => !plain(item) || !validId(item.question_id)))
        throw new Error('復習データの問題IDが不正です。');
      oldItems = Object.fromEntries(
        oldItems
          .filter((item) => plain(item) && validId(item.question_id))
          .map((item) => [item.question_id, item]),
      );
    }
    if (!plain(oldItems)) {
      if (strict) throw new Error('復習データの形式が正しくありません。');
      oldItems = {};
    }
    for (const [id, item] of Object.entries(oldItems)) {
      if (!validId(id) || !plain(item)) {
        if (strict) throw new Error('復習データの問題IDが不正です。');
        continue;
      }
      const due = asDate(item.due_at);
      const ease = item.easiness,
        interval = item.interval_hours,
        repetitions = item.repetitions,
        lapses = item.lapses;
      if (
        !due ||
        !Number.isFinite(ease) ||
        ease < 1.3 ||
        ease > 2.85 ||
        !Number.isFinite(interval) ||
        interval <= 0 ||
        interval > 24 * 365 * 100 ||
        !Number.isInteger(repetitions) ||
        repetitions < 0 ||
        !Number.isInteger(lapses) ||
        lapses < 0
      )
        continue;
      result.learningItems[id] = {
        ...item,
        question_id: id,
        due_at: due.toISOString(),
      };
    }
    const rebuild = new Set();
    for (let i = 0; i < result.answers.length; i += 1) {
      const answer = result.answers[i];
      if (
        recompute ||
        !result.learningItems[answer.question_id] ||
        regraded.has(answer.question_id)
      )
        rebuild.add(answer.question_id);
    }
    for (const id of rebuild) if (byId.has(id)) delete result.learningItems[id];
    for (const answer of result.answers) {
      const q = byId.get(answer.question_id);
      if (q && rebuild.has(q.id))
        result.learningItems[q.id] = {
          question_id: q.id,
          chapter_id: q.chapter_id,
          ...updateSchedule(
            result.learningItems[q.id],
            answer.correct,
            answer.elapsed_ms,
            q.difficulty,
            asDate(answer.answered_at),
          ),
        };
    }
    result.recent = result.answers
      .slice(-20)
      .reverse()
      .map((row) => ({
        correct: row.correct,
        label: row.chapter_id,
        answered_at: row.answered_at,
      }));
    result.session = normalizeSession(source.session, strict);
    return result;
  }

  try {
    if (storage && typeof storage.getItem === 'function') {
      const saved = storage.getItem(STORAGE_KEY);
      if (saved) state = normalizeState(readJson(saved));
    }
  } catch (error) {
    persistenceError = `保存済みの学習履歴を読み込めませんでした: ${error.message || error}`;
  }

  // Historical answers may grow for years. Index them once on load/import and
  // update only the newly submitted answer. Rendering no longer scans all history.
  let answerIndex;

  function indexAnswer(answer) {
    if (!byId.has(answer.question_id)) return;
    answerIndex.answers.push(answer);
    answerIndex.correct += Number(answer.correct);
    const item = answerIndex.summary.get(answer.question_id) || {
      attempts: 0,
      correct: 0,
      elapsed: 0,
    };
    item.attempts += 1;
    item.correct += Number(answer.correct);
    item.elapsed += answer.elapsed_ms;
    item.last_correct = answer.correct;
    item.last_answered_at = answer.answered_at;
    answerIndex.summary.set(answer.question_id, item);
    const key = dayKey(asDate(answer.answered_at));
    const day = answerIndex.days.get(key) || { count: 0, correct: 0 };
    day.count += 1;
    day.correct += Number(answer.correct);
    answerIndex.days.set(key, day);
  }

  function rebuildAnswerIndex() {
    answerIndex = { answers: [], summary: new Map(), days: new Map(), correct: 0 };
    for (const answer of state.answers) indexAnswer(answer);
  }
  rebuildAnswerIndex();

  const activeAnswers = () => answerIndex.answers;
  const summaries = () => answerIndex.summary;

  function dueAt(id) {
    return asDate(state.learningItems[id]?.due_at);
  }

  function reviewItems(date = clock(), summary = summaries()) {
    return bank
      .flatMap((q) => {
        const item = summary.get(q.id);
        const due = dueAt(q.id);
        const overdue = due && due.getTime() <= date.getTime();
        if (!overdue && item?.last_correct !== false) return [];
        return [
          {
            ...q,
            reason: overdue ? 'due' : 'wrong',
            due_at: due?.toISOString() || null,
            last_correct: item?.last_correct ?? null,
          },
        ];
      })
      .sort((a, b) => {
        if (a.reason !== b.reason) return a.reason === 'due' ? -1 : 1;
        if (a.reason === 'due')
          return (asDate(a.due_at)?.getTime() || 0) - (asDate(b.due_at)?.getTime() || 0);
        return (summary.get(b.id)?.last_answered_at || '').localeCompare(
          summary.get(a.id)?.last_answered_at || '',
        );
      });
  }

  function getStats() {
    const date = clock(),
      today = dayKey(date);
    const { answers, summary, days, correct } = answerIndex;
    let streak = 0,
      cursor = new Date(`${today}T00:00:00Z`);
    if (!days.has(today)) cursor = new Date(cursor.getTime() - DAY);
    while (days.has(cursor.toISOString().slice(0, 10))) {
      streak += 1;
      cursor = new Date(cursor.getTime() - DAY);
    }
    const week = Array.from({ length: 7 }, (_, index) => {
      const day = new Date(new Date(`${today}T00:00:00Z`).getTime() - (6 - index) * DAY);
      const key = day.toISOString().slice(0, 10);
      return {
        date: key,
        label: ['日', '月', '火', '水', '木', '金', '土'][day.getUTCDay()],
        count: days.get(key)?.count || 0,
        correct: days.get(key)?.correct || 0,
      };
    });
    return {
      total: answers.length,
      correct,
      accuracy: answers.length ? correct / answers.length : 0,
      todayCount: days.get(today)?.count || 0,
      todayCorrect: days.get(today)?.correct || 0,
      streak,
      dueCount: bank.filter((q) => {
        const due = dueAt(q.id);
        return due && due <= date;
      }).length,
      weakCount: [...summary.values()].filter((item) => item.last_correct === false).length,
      unseenCount: bank.length - summary.size,
      learnedCount: summary.size,
      bookmarkCount: state.bookmarks.filter((id) => byId.has(id)).length,
      dailyGoal: state.dailyGoal,
      week,
      ...(persistenceError ? { storageError: persistenceError } : {}),
    };
  }

  function getChapters() {
    const summary = summaries(),
      date = clock(),
      chapters = new Map();
    for (const q of bank) {
      const key = chapterKey(q);
      const row = chapters.get(key) || {
        id: q.chapter_id,
        group: q.chapter_group,
        name: q.chapter_id,
        key,
        total: 0,
        seen: 0,
        correct: 0,
        attempts: 0,
        accuracy: 0,
        due: 0,
        weak: 0,
      };
      const item = summary.get(q.id);
      row.total += 1;
      row.seen += Number(Boolean(item));
      row.correct += item?.correct || 0;
      row.attempts += item?.attempts || 0;
      const due = dueAt(q.id);
      row.due += Number(Boolean(due && due <= date));
      row.weak += Number(item?.last_correct === false);
      row.accuracy = row.attempts ? row.correct / row.attempts : 0;
      chapters.set(key, row);
    }
    return [...chapters.values()].sort(
      (a, b) =>
        a.name.localeCompare(b.name, 'ja', { numeric: true }) ||
        a.group.localeCompare(b.group, 'ja'),
    );
  }

  function buildSession({ mode = 'daily', chapter = null, limit = 10, ids = null } = {}) {
    if (!MODES.has(mode)) throw new Error('学習モードが正しくありません。');
    if (!Number.isInteger(limit) || limit < 0 || limit > 10000)
      throw new Error('出題数が正しくありません。');
    if (ids !== null && (!Array.isArray(ids) || ids.some((id) => !validId(id))))
      throw new Error('出題する問題IDが正しくありません。');
    const date = clock(),
      summary = summaries(),
      selected = [],
      used = new Set(),
      perChapter = new Map();
    const matches = (q) =>
      chapter == null ||
      (typeof chapter === 'string'
        ? q.chapter_id === chapter || chapterKey(q) === chapter
        : q.chapter_id === chapter.id &&
          (chapter.group == null || q.chapter_group === chapter.group));
    let pool = bank.filter(matches);
    if (mode === 'chapter' && chapter == null && ids === null) pool = [];
    const review = reviewItems(date, summary);
    if (mode === 'review') {
      const reviewIds = new Set(review.map((q) => q.id));
      pool = pool.filter((q) => reviewIds.has(q.id));
    }
    if (mode === 'bookmarked') pool = pool.filter((q) => state.bookmarks.includes(q.id));
    const eligible = new Set(pool.map((q) => q.id));
    const add = (id) => {
      if (selected.length >= limit || used.has(id) || !eligible.has(id)) return;
      selected.push(id);
      used.add(id);
      const key = chapterKey(byId.get(id));
      perChapter.set(key, (perChapter.get(key) || 0) + 1);
    };
    if (ids !== null) ids.forEach(add);
    else {
      review.filter((q) => q.reason === 'due').forEach((q) => add(q.id));
      if (mode === 'review') review.forEach((q) => add(q.id));
      else {
        const chapterAttempts = new Map();
        for (const q of pool)
          chapterAttempts.set(
            chapterKey(q),
            (chapterAttempts.get(chapterKey(q)) || 0) + (summary.get(q.id)?.attempts || 0),
          );
        const jitter = new Map(pool.map((q) => [q.id, clamp(Number(random()) || 0, 0, 1) * 0.015]));
        while (selected.length < limit && selected.length < pool.length) {
          let best = null,
            bestScore = -Infinity;
          for (const q of pool) {
            if (used.has(q.id)) continue;
            const item = summary.get(q.id),
              key = chapterKey(q);
            const mastery = item ? wilson(item.correct, item.attempts) : 0;
            const priority = !item
              ? 1
              : item.last_correct === false
                ? 1.25
                : 0.25 + (1 - mastery) * 0.25;
            const score =
              priority -
              (perChapter.get(key) || 0) * 0.65 -
              Math.min(0.2, (chapterAttempts.get(key) || 0) / 200) +
              jitter.get(q.id);
            if (score > bestScore) {
              best = q;
              bestScore = score;
            }
          }
          if (!best) break;
          add(best.id);
        }
      }
    }
    return {
      mode,
      ids: selected,
      index: 0,
      answers: [],
      startedAt: date.toISOString(),
      ...(chapter != null ? { chapter: clone(chapter) } : {}),
    };
  }

  function recordAnswer(questionId, choiceIndex, elapsedMs = 0) {
    const q = byId.get(questionId);
    if (!q) throw new Error('問題が見つかりません。');
    if (!Number.isInteger(choiceIndex) || choiceIndex < 0 || choiceIndex >= q.choices.length)
      throw new Error('選択肢が正しくありません。');
    if (typeof elapsedMs !== 'number' || !Number.isFinite(elapsedMs) || elapsedMs < 0)
      throw new Error('回答時間が正しくありません。');
    // Returning to a question after a suspended tab must not block submission.
    elapsedMs = Math.min(HOUR, elapsedMs);
    const date = clock(),
      correct = choiceIndex === q.correct_index;
    const row = {
      question_id: q.id,
      chapter_id: q.chapter_id,
      selected_index: choiceIndex,
      chosen_index: choiceIndex,
      correct,
      elapsed_ms: Math.round(elapsedMs),
      answered_at: date.toISOString(),
    };
    state.answers.push(row);
    indexAnswer(row);
    state.learningItems[q.id] = {
      question_id: q.id,
      chapter_id: q.chapter_id,
      ...updateSchedule(state.learningItems[q.id], correct, elapsedMs, q.difficulty, date),
    };
    state.recent.unshift({
      correct,
      label: q.chapter_id,
      answered_at: row.answered_at,
    });
    state.recent = state.recent.slice(0, 20);
    return {
      correct,
      correctIndex: q.correct_index,
      explanation: q.explanation,
      dueAt: state.learningItems[q.id].due_at,
    };
  }

  function answer(questionId, choiceIndex, elapsedMs = 0) {
    const result = recordAnswer(questionId, choiceIndex, elapsedMs);
    persist();
    return result;
  }

  function answerCurrentQuestion(choiceIndex, elapsedMs = 0) {
    const current = state.session;
    const questionId = current?.ids[current.index];
    if (!questionId) throw new Error('進行中の問題が見つかりません。');
    if (current.answers.some((row) => row.questionId === questionId)) {
      throw new Error('この問題は回答済みです。');
    }
    const result = recordAnswer(questionId, choiceIndex, elapsedMs);
    current.answers.push({
      questionId,
      choiceIndex,
      correct: result.correct,
      dueAt: result.dueAt,
      elapsedMs: Math.round(Math.min(HOUR, elapsedMs)),
    });
    // Commit the history and its displayed feedback together. A reload cannot see
    // a recorded answer with an unanswered session, and each answer writes once.
    persist();
    return clone(current);
  }

  const engine = {
    bank,
    meta,
    chapterCount,
    searchQuestions(query) {
      const normalized = String(query).trim().toLocaleLowerCase('ja');
      return searchIndex
        .filter(({ text }) => text.includes(normalized))
        .map(({ question }) => question);
    },
    get state() {
      return state;
    },
    get persistenceError() {
      return persistenceError;
    },
    getStats,
    getChapters,
    getReviewItems: () => reviewItems(),
    getHistory: () =>
      activeAnswers()
        .map((row) => ({ ...row }))
        .reverse(),
    getQuestion: (id) => byId.get(id) || null,
    buildSession,
    answer,
    answerCurrentQuestion,
    toggleBookmark(id) {
      if (!byId.has(id)) throw new Error('問題が見つかりません。');
      const selected = !state.bookmarks.includes(id);
      state.bookmarks = selected
        ? [...state.bookmarks, id]
        : state.bookmarks.filter((value) => value !== id);
      persist();
      return selected;
    },
    isBookmarked: (id) => byId.has(id) && state.bookmarks.includes(id),
    setDailyGoal(n) {
      if (!Number.isInteger(n) || n < 1 || n > 200)
        throw new Error('1日の目標は1〜200問にしてください。');
      state.dailyGoal = n;
      persist();
      return n;
    },
    exportData() {
      return {
        schema_version: EXPORT_SCHEMA,
        bank_version: version,
        exported_at: clock().toISOString(),
        state: clone(state),
      };
    },
    importData(input) {
      const data = readJson(input);
      if (!plain(data)) throw new Error('学習データの形式が正しくありません。');
      if (data.schema_version !== undefined && data.schema_version !== EXPORT_SCHEMA)
        throw new Error('対応していないバックアップ形式です。');
      if (own(data, 'state') && !plain(data.state))
        throw new Error('バックアップ内の学習データが不正です。');
      const source = own(data, 'state') ? data.state : data;
      if (!Array.isArray(source.answers) && !Array.isArray(source.learning_items))
        throw new Error('回答履歴を含むバックアップを選択してください。');
      const imported = normalizeState(source, true, true);
      state = imported;
      rebuildAnswerIndex();
      persist();
      return {
        answers: activeAnswers().length,
        bookmarks: state.bookmarks.filter((id) => byId.has(id)).length,
        retainedInactive: state.answers.filter((row) => !byId.has(row.question_id)).length,
      };
    },
    saveSession(session) {
      assertSafeJson(session);
      state.session = normalizeSession(session, true);
      persist();
      return engine.getSession();
    },
    getSession: () => (state.session ? clone(state.session) : null),
    clearSession() {
      state.session = null;
      persist();
    },
  };
  return engine;
}
