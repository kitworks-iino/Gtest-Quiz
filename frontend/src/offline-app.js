import {
  icon,
  escapeHtml,
  encodeId,
  formatPercent,
  dateKey,
  today,
  fullDate,
  timeLabel,
  dueLabel,
  iconButton,
  primaryButton,
  listRow,
  pageHeader,
  settingsRow,
} from './ui.js';
import { createEngine } from './learning-engine.js';
import { partitionQuestions } from './content-guard.js';
const STATIC_BANK_URL = './question-bank.json';
const HOLD_URL = './question-review-holds.json';
const app = document.querySelector('#app'),
  screen = document.querySelector('#screen'),
  navigation = document.querySelector('#navigation'),
  overlayRoot = document.querySelector('#overlay-root');
const difficultyNames = { basic: '基礎', standard: '標準', advanced: '応用' };
const modeNames = {
  daily: '今日の学習',
  review: '復習',
  bookmarked: 'あとで復習',
  chapter: '章別学習',
};
const letters = ['A', 'B', 'C', 'D'];
let engine = null,
  view = 'home',
  session = null,
  questionStarted = 0,
  resultSession = null,
  reviewFilter = 'all',
  searchQuery = '',
  searchLimit = 40,
  selectedChapter = null,
  inspectId = null,
  inspectRecord = null,
  inspectBack = 'history',
  pendingImport = null,
  pendingStart = null,
  heldCount = 0,
  sourceCount = 0,
  booting = false,
  toastTimer,
  lastFocus = null;
function toast(message) {
  const el = document.querySelector('#toast');
  el.textContent = message;
  el.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('visible'), 3200);
}
function persistence() {
  document.querySelector('#storage-warning').hidden = !engine?.persistenceError;
}
function renderNav() {
  if (!engine || view === 'quiz') return;
  const stats = engine.getStats();
  const active =
    {
      history: 'home',
      chapter: 'search',
      inspect: inspectBack === 'history' ? 'home' : 'search',
      summary: 'home',
    }[view] || view;
  navigation.innerHTML = [
    ['home', '今日の学習', 'home'],
    ['inbox', '復習', 'review'],
    ['create', '学習を始める', 'study'],
    ['search', '問題・章を探す', 'search'],
    ['settings', '設定', 'settings'],
  ]
    .map(
      ([i, label, a]) =>
        `<button
          data-action="${a}"
          aria-label="${label}"
          class="${active === a ? 'active' : ''}"
          ${active === a ? 'aria-current="page"' : ''}
        >
          ${icon(i)}${a === 'review' && stats.dueCount ? '<i class="nav-dot"></i>' : ''}<span
            class="visually-hidden"
            >${label}</span
          >
        </button>`,
    )
    .join('');
}
function render({ resetScroll = true } = {}) {
  if (!engine) return;
  app.classList.toggle('quiz-mode', view === 'quiz');
  app.querySelector('.quiz-footer')?.remove();
  renderNav();
  const oldScroll = screen.querySelector('.page')?.scrollTop || 0;
  switch (view) {
    case 'quiz':
      renderQuestion();
      break;
    case 'review':
      renderReview();
      break;
    case 'search':
      renderSearch();
      break;
    case 'chapter':
      renderChapter();
      break;
    case 'history':
      renderHistory();
      break;
    case 'settings':
      renderSettings();
      break;
    case 'summary':
      renderSummary();
      break;
    case 'inspect':
      renderInspect();
      break;
    default:
      renderHome();
  }
  if (!resetScroll && screen.querySelector('.page'))
    screen.querySelector('.page').scrollTop = oldScroll;
  persistence();
}
function navigate(next) {
  closeSheet();
  view = next;
  render();
  screen.focus({ preventScroll: true });
}
function weekMarkup(stats) {
  return `<div class="week" aria-label="過去7日の学習記録">
    ${stats.week
      .map(
        (d) =>
          `<div
            class="week-day ${d.count ? 'done' : ''} ${d.date === today() ? 'today' : ''}"
            aria-label="${d.date} ${d.count}問"
          >
            <div class="week-mark">${d.count ? icon('check') : ''}</div>
            <span class="week-label">${d.label}</span>
          </div>`,
      )
      .join('')}
  </div>`;
}
// Home, review and study sessions.
function hasSessionProgress(saved) {
  return saved && saved.answers.length > 0 && saved.index < saved.ids.length;
}
function renderHome() {
  const s = engine.getStats(),
    saved = engine.getSession(),
    inProgress = hasSessionProgress(saved);
  const remain = Math.max(0, s.dailyGoal - s.todayCount);
  screen.innerHTML = `<section class="page">
    ${pageHeader('今日の学習')}
    <div class="stats-strip">
      <div>
        <div class="stat-number">${s.todayCount}<small>問</small></div>
        <div class="stat-label">今日の回答</div>
      </div>
      <div>
        <div class="stat-number">${s.streak}<small>日</small></div>
        <div class="stat-label">連続学習</div>
      </div>
      <div>
        <div class="stat-number">${s.total ? formatPercent(s.accuracy) : '—'}</div>
        <div class="stat-label">累計正答率</div>
      </div>
    </div>
    <div
      class="daily-progress"
      role="progressbar"
      aria-label="1日の目標"
      aria-valuemin="0"
      aria-valuemax="${s.dailyGoal}"
      aria-valuenow="${Math.min(s.todayCount, s.dailyGoal)}"
    >
      <span style="width:${Math.min(100, (s.todayCount / s.dailyGoal) * 100)}%"></span>
    </div>
    <div class="progress-description">
      <span>${fullDate(new Date())}</span
      ><span>${remain ? `目標まであと${remain}問` : '今日の目標を達成'}</span>
    </div>
    <div class="start-block">
      ${primaryButton(inProgress ? '続きから学習する' : remain ? `今日の${Math.min(remain, s.dailyGoal)}問を始める` : 'もう少し学習する', inProgress ? 'resume' : 'start:daily')}
      <div class="start-note">
        ${inProgress ? `${modeNames[saved.mode]} · ${saved.answers.length} / ${saved.ids.length}問回答済み` : '1問ずつ回答して、その場で解説を確認'}
      </div>
    </div>
    <h2 class="section-title">学習メニュー</h2>
    ${listRow({ name: 'clock', title: '復習待ち', sub: s.dueCount ? '前回の回答から時間をあけて、もう一度' : '復習のタイミングになった問題をまとめます', action: 'review', count: s.dueCount ? `${s.dueCount}問` : '', tone: 'amber', dot: !!s.dueCount })}${listRow({ name: 'focus', title: '間違えた問題', sub: s.weakCount ? '前回間違えた問題を解き直す' : '回答後、間違えた問題がここに集まります', action: 'review-filter:wrong', count: s.weakCount ? `${s.weakCount}問` : '', tone: 'red' })}${listRow({ name: 'list', title: '章を選んで学ぶ', sub: `${engine.chapterCount}章 · ${engine.bank.length}問を収録`, action: 'search', tone: 'blue' })}
    <h2 class="section-title">
      この7日間<button data-action="history">学習記録 ${icon('chevron')}</button>
    </h2>
    ${weekMarkup(s)}
    <p class="mini-summary">
      ${s.week.reduce((n, d) => n + d.count, 0)}問回答 · ${s.learnedCount} /
      ${engine.bank.length}問を学習済み
    </p>
  </section>`;
}
function reviewQuestions() {
  if (reviewFilter === 'bookmarked') return engine.bank.filter((q) => engine.isBookmarked(q.id));
  const rows = engine.getReviewItems();
  return rows.filter((q) =>
    reviewFilter === 'due'
      ? q.reason === 'due'
      : reviewFilter === 'wrong'
        ? q.last_correct === false
        : true,
  );
}
function questionRow(q, { action = 'open', label = '', tone = 'gray' } = {}) {
  return `<button class="list-row review-item" data-action="${action}:${encodeId(q.id)}">
    <span class="row-icon ${tone}"
      >${icon(label === 'あとで復習' ? 'bookmark' : label === '復習の時間' ? 'clock' : 'document')}</span
    ><span class="row-copy"
      ><span class="row-title">${escapeHtml(q.question)}</span
      ><span class="row-subtitle"
        >${label ? escapeHtml(label) + ' · ' : ''}${escapeHtml(q.chapter_id)}</span
      ></span
    ><span class="row-end">${icon('chevron')}</span>
  </button>`;
}
function renderReview() {
  const s = engine.getStats(),
    items = reviewQuestions();
  screen.innerHTML = `<section class="page">
    ${pageHeader('復習', { action: 'history', actionIcon: 'clock', actionLabel: '学習記録を開く' })}
    <div class="filter-tabs" role="tablist" aria-label="復習する問題">
      ${[
        ['all', 'すべて', ''],
        ['due', '期限', s.dueCount],
        ['wrong', '間違い', s.weakCount],
        ['bookmarked', '保存', s.bookmarkCount],
      ]
        .map(
          ([v, l, n]) =>
            `<button
              class="filter-tab ${reviewFilter === v ? 'active' : ''}"
              data-action="review-filter:${v}"
              role="tab"
              aria-selected="${reviewFilter === v}"
            >
              ${l}${n !== '' ? `<small>${n}</small>` : ''}
            </button>`,
        )
        .join('')}
    </div>
    ${
      items.length
        ? `<div class="review-start">
              ${primaryButton(`${Math.min(10, items.length)}問を復習する`, 'start-review')}
            </div>
            ${items.map((q) => questionRow(q, { label: reviewFilter === 'bookmarked' ? 'あとで復習' : q.reason === 'due' ? '復習の時間' : '前回は不正解', tone: q.reason === 'due' ? 'amber' : 'gray' })).join('')}`
        : `<div class="empty-state">
            ${icon(reviewFilter === 'bookmarked' ? 'bookmark' : 'check')}
            <h2>
              ${reviewFilter === 'bookmarked' ? '保存した問題はありません' : 'いま復習する問題はありません'}
            </h2>
            <p>
              ${reviewFilter === 'bookmarked' ? '学習画面右上のしおりで、<br>あとで見返す問題を保存できます。' : '学習を進めると、復習する問題と<br>そのタイミングが自動で決まります。'}
            </p>
            ${primaryButton('今日の学習を始める', 'start:daily')}
          </div>`
    }
  </section>`;
}
function startSession(options, force = false) {
  const saved = engine.getSession();
  if (!force && hasSessionProgress(saved)) {
    showSheet(
      '途中の学習があります',
      `<p>
          ${modeNames[saved.mode]}：全${saved.ids.length}問中${saved.answers.length}問に回答済みです。
        </p>
        ${primaryButton('続きから学習する', 'resume')}<button
          class="secondary-button"
          data-action="replace-session"
        >
          新しいセットを始める
        </button>`,
    );
    pendingStart = options;
    return;
  }
  session = engine.buildSession(options);
  if (!session.ids.length) {
    toast('この条件で出題できる問題はありません');
    return;
  }
  engine.saveSession(session);
  resultSession = null;
  questionStarted = Date.now();
  closeSheet();
  view = 'quiz';
  render();
}
function startDaily() {
  const s = engine.getStats();
  const remaining = s.dailyGoal - s.todayCount;
  startSession({
    mode: 'daily',
    limit: remaining > 0 ? Math.min(s.dailyGoal, remaining) : s.dailyGoal,
  });
}
function resume() {
  session = engine.getSession();
  if (!session) {
    startDaily();
    return;
  }
  closeSheet();
  questionStarted = Date.now();
  view = session.index >= session.ids.length ? 'summary' : 'quiz';
  if (view === 'summary') resultSession = session;
  render();
}
function currentQuestion() {
  return session ? engine.getQuestion(session.ids[session.index]) : null;
}
function recordFor(q) {
  return session?.answers.find((a) => a.questionId === q.id) || null;
}
function safeResult(q, record) {
  return record ? { ...record, correct: record.choiceIndex === q.correct_index } : null;
}
function renderSourcePanel(q) {
  const fields = [
    ['source_title', '出典'],
    ['source_version', '版'],
    ['source_checked_at', '確認日'],
    ['legal_basis', '根拠'],
  ].filter(([k]) => q[k]);
  let url = '';
  try {
    const parsed = new URL(q.source_url);
    if (['http:', 'https:'].includes(parsed.protocol)) url = parsed.href;
  } catch {}
  if (!fields.length && !url) return '';
  return `<details class="source-panel">
    <summary>出典・根拠を確認</summary>
    ${fields.map(([key, label]) => `<p>${label}：${escapeHtml(q[key])}</p>`).join('')}${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">参照元を開く ${icon('share')}</a>` : ''}
  </details>`;
}
function feedbackMarkup(q, record) {
  const r = safeResult(q, record);
  return `<section class="feedback" id="answer-feedback" tabindex="-1" aria-label="回答結果">
    <div class="feedback-head ${r.correct ? '' : 'wrong'}">
      ${icon(r.correct ? 'check' : 'close')}${r.correct ? '正解です' : 'もう一度、確認しましょう'}
    </div>
    <p class="correct-answer">
      正解：${letters[q.correct_index]}　${escapeHtml(q.choices[q.correct_index])}
    </p>
    <h2 class="explanation-label">解説</h2>
    <p class="explanation">${escapeHtml(q.explanation)}</p>
    ${r.dueAt ? `<p class="review-time">${icon('clock')}次の復習：${escapeHtml(dueLabel(r.dueAt))}</p>` : ''}${renderSourcePanel(q)}
  </section>`;
}
function choiceMarkup(q, record, readOnly = false) {
  return `<div class="choices" role="group" aria-label="選択肢">
    ${q.choices
      .map((choice, index) => {
        const correct = record && index === q.correct_index,
          wrong = record && record.choiceIndex === index && !correct;
        return `<button
          class="choice ${correct ? 'right' : wrong ? 'wrong' : record ? 'dim' : ''}"
          data-action="answer:${index}"
          ${record || readOnly ? 'disabled' : ''}
          aria-label="${letters[index]} ${escapeHtml(choice)}${correct ? ' 正解' : wrong ? ' 選んだ回答 不正解' : ''}"
        >
          <span class="choice-letter">${letters[index]}</span
          ><span class="choice-text">${escapeHtml(choice)}</span
          >${correct || wrong ? `<span class="choice-result">${icon(correct ? 'check' : 'close')}</span>` : ''}
        </button>`;
      })
      .join('')}
  </div>`;
}
function renderQuestion() {
  const q = currentQuestion();
  if (!q) {
    finishSession();
    return;
  }
  const record = recordFor(q),
    n = session.index + 1;
  screen.innerHTML = `<section class="page question-page">
    <header class="quiz-top">
      ${iconButton('back', '学習を中断して戻る', 'pause')}
      <div class="quiz-top-center">
        ${n} / ${session.ids.length}<span>${modeNames[session.mode]}</span>
      </div>
      ${iconButton('bookmark', engine.isBookmarked(q.id) ? '保存を解除' : 'あとで復習に保存', 'bookmark:' + encodeId(q.id), engine.isBookmarked(q.id) ? 'bookmarked' : '')}
    </header>
    <div
      class="quiz-progress"
      role="progressbar"
      aria-label="セットの進捗"
      aria-valuemin="0"
      aria-valuemax="${session.ids.length}"
      aria-valuenow="${session.answers.length}"
    >
      <span style="width:${(session.answers.length / session.ids.length) * 100}%"></span>
    </div>
    <article class="question-content" data-question-id="${escapeHtml(q.id)}">
      <div class="question-meta">
        <span class="tag">${escapeHtml(q.chapter_id)}</span
        ><span class="tag accent">${difficultyNames[q.difficulty]}</span>
      </div>
      <h1 class="question-title">${escapeHtml(q.question)}</h1>
      ${choiceMarkup(q, record)}${record ? feedbackMarkup(q, record) : '<p class="answer-hint">正しいと思う選択肢を1つタップしてください</p>'}
    </article>
  </section>`;
  const footer = document.createElement('div');
  footer.className = 'quiz-footer';
  footer.innerHTML = `<button class="primary" data-action="next" ${record ? '' : 'disabled'}>
    ${record ? (n === session.ids.length ? '結果を見る' : '次の問題へ') : '選択肢を選んでください'}${record ? icon('chevron') : ''}
  </button>`;
  app.append(footer);
}
function submitAnswer(index) {
  const q = currentQuestion();
  if (view !== 'quiz' || !q || recordFor(q)) return;
  const elapsedMs = Math.max(0, Date.now() - questionStarted);
  session = engine.answerCurrentQuestion(index, elapsedMs);
  render({ resetScroll: false });
  const feedback = screen.querySelector('#answer-feedback');
  feedback?.scrollIntoView?.({
    behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      ? 'instant'
      : 'smooth',
    block: 'start',
  });
  feedback?.focus({ preventScroll: true });
}
function nextQuestion() {
  const q = currentQuestion();
  if (!q || !recordFor(q)) return;
  session.index++;
  if (session.index >= session.ids.length) {
    finishSession();
    return;
  }
  engine.saveSession(session);
  questionStarted = Date.now();
  render();
}
function finishSession() {
  if (!session) return;
  resultSession = JSON.parse(JSON.stringify(session));
  engine.clearSession();
  view = 'summary';
  render();
}
function renderSummary() {
  const s = resultSession || session;
  if (!s) {
    view = 'home';
    renderHome();
    return;
  }
  const rows = s.answers
      .map((a) => ({ a, q: engine.getQuestion(a.questionId) }))
      .filter((x) => x.q),
    correct = rows.filter(({ a, q }) => a.choiceIndex === q.correct_index).length,
    wrong = rows.length - correct;
  screen.innerHTML = `<section class="page">
    ${pageHeader('学習の記録', { action: 'history', actionIcon: 'clock', actionLabel: 'すべての学習記録' })}
    <div class="results-icon">${icon('check')}</div>
    <h1 class="results-title">${s.ids.length}問、おつかれさまでした</h1>
    <p class="results-sub">
      ${wrong ? `${wrong}問を復習リストに追加しました。<br />解説を読んで、もう一度確かめましょう。` : 'すべて正解です。<br>時間をあけた復習で、知識を定着させましょう。'}
    </p>
    <div class="result-score">${correct}<small> / ${rows.length}</small></div>
    <p class="result-score-caption">正解した問題</p>
    <div class="results-actions">
      ${wrong ? primaryButton('間違えた問題を解き直す', 'retry-wrong') : primaryButton('さらに10問学ぶ', 'extra-study')}${primaryButton('今日の学習に戻る', 'home', 'outline')}
    </div>
    <h2 class="section-title">今回の回答</h2>
    ${rows.map(({ a, q }) => `<button class="result-row ${a.choiceIndex === q.correct_index ? '' : 'wrong'}" data-action="inspect:${encodeId(q.id)}">${icon(a.choiceIndex === q.correct_index ? 'check' : 'close')}<span>${escapeHtml(q.question)}</span></button>`).join('')}
  </section>`;
}
// Question discovery and saved learning records.
function renderSearch() {
  screen.innerHTML = `<section class="page">
    ${pageHeader('問題を探す', { action: 'bookmarks', actionIcon: 'bookmark', actionLabel: '保存した問題' })}<label
      class="search-box"
      >${icon('search')}<input
        id="search-input"
        type="search"
        aria-label="問題・章を検索"
        placeholder="キーワード・章名で検索"
        value="${escapeHtml(searchQuery)}"
        autocomplete="off"
    /></label>
    <div id="search-results"></div>
  </section>`;
  renderSearchResults();
  screen.querySelector('#search-input').addEventListener('input', (e) => {
    searchQuery = e.target.value;
    searchLimit = 40;
    renderSearchResults();
  });
}
function renderSearchResults() {
  const host = screen.querySelector('#search-results');
  if (!host) return;
  const query = searchQuery.trim().toLocaleLowerCase('ja');
  if (query) {
    const hits = engine.searchQuestions(query);
    host.innerHTML = `<div class="search-result-count">${hits.length}問が見つかりました</div>
      ${hits
        .slice(0, searchLimit)
        .map((q) =>
          questionRow(q, {
            action: 'open',
            label: difficultyNames[q.difficulty],
          }),
        )
        .join(
          '',
        )}${hits.length > searchLimit ? '<button class="load-more" data-action="more-search">さらに表示する</button>' : ''}${!hits.length ? '<p class="plain-note">別のキーワードで検索してください。</p>' : ''}`;
    return;
  }
  const groups = new Map();
  engine.getChapters().forEach((c) => {
    if (!groups.has(c.group)) groups.set(c.group, []);
    groups.get(c.group).push(c);
  });
  host.innerHTML = `<div class="search-result-count">
      ${engine.chapterCount}章 · ${engine.bank.length}問
    </div>
    ${[...groups]
      .map(
        ([g, chapters]) =>
          `<section class="chapter-group">
            <h2 class="section-title">${escapeHtml(g)}</h2>
            ${chapters
              .map(
                (c) =>
                  `<button class="chapter-row" data-action="chapter:${encodeId(c.key)}">
                    <span class="chapter-index"
                      >${escapeHtml(c.name.match(/^\d+/)?.[0] || '—')}</span
                    ><span class="row-copy"
                      ><span class="row-title"
                        >${escapeHtml(c.name.replace(/^\d+[.．]\s*/, ''))}</span
                      ><span class="row-subtitle"
                        >${c.total}問 ·
                        ${c.seen}問学習済み${c.attempts ? ` · 正答率${formatPercent(c.accuracy)}` : ''}</span
                      ></span
                    ><span
                      class="chapter-ring"
                      style="--progress:${(c.seen / c.total) * 100}%"
                      aria-hidden="true"
                    ></span>
                  </button>`,
              )
              .join('')}
          </section>`,
      )
      .join('')}`;
}
function renderChapter() {
  const c = engine.getChapters().find((c) => c.key === selectedChapter);
  if (!c) {
    view = 'search';
    renderSearch();
    return;
  }
  const questions = engine.bank.filter((q) => q.chapter_id === c.id && q.chapter_group === c.group);
  screen.innerHTML = `<section class="page">
    ${pageHeader(c.name.replace(/^\d+[.．]\s*/, ''), { back: true, eyebrow: c.group, action: null })}
    <div class="stats-strip">
      <div>
        <div class="stat-number">${c.total}<small>問</small></div>
        <div class="stat-label">収録問題</div>
      </div>
      <div>
        <div class="stat-number">${c.seen}<small>問</small></div>
        <div class="stat-label">学習済み</div>
      </div>
      <div>
        <div class="stat-number">${c.attempts ? formatPercent(c.accuracy) : '—'}</div>
        <div class="stat-label">正答率</div>
      </div>
    </div>
    <div class="start-block">
      ${primaryButton(`この章を${Math.min(10, c.total)}問学ぶ`, 'start-chapter')}
    </div>
    <h2 class="section-title">この章の問題</h2>
    ${questions.map((q) => questionRow(q, { label: difficultyNames[q.difficulty] })).join('')}
  </section>`;
}
function renderHistory() {
  const s = engine.getStats(),
    history = engine.getHistory();
  const days = new Map();
  history.forEach((h, historyIndex) => {
    const day = dateKey(h.answered_at);
    if (!days.has(day)) days.set(day, []);
    days.get(day).push({ ...h, historyIndex });
  });
  screen.innerHTML = `<section class="page">
    ${pageHeader('学習記録', { back: true, action: null })}
    <div class="stats-strip">
      <div>
        <div class="stat-number">${s.total}<small>回</small></div>
        <div class="stat-label">累計回答</div>
      </div>
      <div>
        <div class="stat-number">${s.total ? formatPercent(s.accuracy) : '—'}</div>
        <div class="stat-label">正答率</div>
      </div>
      <div>
        <div class="stat-number">${s.streak}<small>日</small></div>
        <div class="stat-label">連続学習</div>
      </div>
    </div>
    ${weekMarkup(s)}${
      history.length
        ? [...days]
            .slice(0, 30)
            .map(
              ([d, rows]) =>
                `<h2 class="history-date">
                    ${fullDate(d + 'T00:00:00+09:00')} · ${rows.length}問
                  </h2>
                  ${rows
                    .slice(0, 100)
                    .map((h) => {
                      const q = engine.getQuestion(h.question_id);
                      return `<button
                        class="result-row ${h.correct ? '' : 'wrong'}"
                        data-action="inspect-history:${h.historyIndex}"
                      >
                        ${icon(h.correct ? 'check' : 'close')}<span
                          >${escapeHtml(q.question)}<br /><small
                            >${timeLabel(h.answered_at)} · ${escapeHtml(q.chapter_id)}</small
                          ></span
                        >
                      </button>`;
                    })
                    .join('')}`,
            )
            .join('')
        : `<div class="empty-state">
            ${icon('chart')}
            <h2>最初の1問から、記録が始まります</h2>
            <p>回答するたびに、自動で保存されます。</p>
            ${primaryButton('学習を始める', 'start:daily')}
          </div>`
    }
  </section>`;
}
function renderInspect() {
  const q = engine.getQuestion(inspectId);
  if (!q) {
    navigate('history');
    return;
  }
  const history = inspectRecord || engine.getHistory().find((a) => a.question_id === q.id);
  const record = history
    ? {
        choiceIndex: history.choiceIndex ?? history.chosen_index ?? history.selected_index,
        dueAt: history.dueAt || null,
      }
    : null;
  screen.innerHTML = `<section class="page">
    ${pageHeader('解説を確認', { back: true, action: 'bookmark:' + encodeId(q.id), actionIcon: 'bookmark', actionLabel: engine.isBookmarked(q.id) ? '保存を解除' : 'あとで復習に保存' })}
    <article class="question-content" data-question-id="${escapeHtml(q.id)}">
      <div class="question-meta">
        <span class="tag">${escapeHtml(q.chapter_id)}</span
        ><span class="tag accent">${difficultyNames[q.difficulty]}</span>
      </div>
      <h1 class="question-title">${escapeHtml(q.question)}</h1>
      ${record ? choiceMarkup(q, record, true) + feedbackMarkup(q, record) : `<p class="plain-note">この問題はまだ回答していません。</p>`}
      <div style="margin-top:26px">
        ${primaryButton('この問題をもう一度解く', 'open:' + encodeId(q.id))}
      </div>
    </article>
  </section>`;
}
// Settings, local backup and dialogs.
function renderSettings() {
  const s = engine.getStats();
  screen.innerHTML = `<section class="page">
    ${pageHeader('設定', { action: null })}
    <h2 class="section-title">学習</h2>
    ${settingsRow('focus', '1日の目標', 'goal', `${s.dailyGoal}問`)}${settingsRow('bookmark', '保存した問題', 'bookmarks', `${s.bookmarkCount}問`)}${settingsRow('clock', '学習記録', 'history')}
    <h2 class="section-title">データ</h2>
    ${settingsRow('download', '学習記録を書き出す', 'export')}${settingsRow('upload', 'バックアップを読み込む', 'import')}${settingsRow('refresh', '問題データを更新する', 'refresh-bank')}
    <div class="storage-description">
      記録は、このブラウザーに保存されます。端末やブラウザーを変えると自動では引き継がれません。「書き出す」で保存したファイルを、新しい端末で読み込んでください。
    </div>
    <h2 class="section-title">このアプリについて</h2>
    ${settingsRow('phone', 'ホーム画面に追加する', 'install')}${settingsRow('document', '問題データと出典', 'about')}
    <p class="bank-info">
      <span class="online-dot ${navigator.onLine === false ? 'offline' : ''}"></span
      >${navigator.onLine === false ? 'オフライン' : 'オンライン'} · ${engine.bank.length}問 /
      ${engine.chapterCount}章<br />${heldCount}問は解説内の選択肢参照を再確認するまで出題を保留しています。
    </p>
    <div class="app-version">Gtest 2.0<br />G検定を、1問ずつ。</div>
  </section>`;
}
function showSheet(title, body) {
  closeSheet();
  lastFocus = document.activeElement;
  overlayRoot.innerHTML = `<div class="overlay">
    <button class="overlay-shade" aria-label="閉じる" data-action="close-sheet"></button>
    <section
      class="sheet"
      role="dialog"
      aria-modal="true"
      aria-labelledby="sheet-title"
      tabindex="-1"
    >
      <div class="sheet-header">
        <h2 id="sheet-title">${escapeHtml(title)}</h2>
        <button data-action="close-sheet">閉じる</button>
      </div>
      ${body}
    </section>
  </div>`;
  overlayRoot.querySelector('.sheet').focus({ preventScroll: true });
  screen.inert = true;
  navigation.inert = true;
}
function closeSheet() {
  if (!overlayRoot.childElementCount) return;
  overlayRoot.innerHTML = '';
  screen.inert = false;
  navigation.inert = false;
  lastFocus?.isConnected && lastFocus.focus?.({ preventScroll: true });
  lastFocus = null;
}
function goalSheet() {
  showSheet(
    '1日の目標',
    `<p>少しずつでも、毎日続けられる量に。</p>
      ${[5, 10, 20, 30].map((n) => `<button class="sheet-option" data-action="set-goal:${n}"><span>${n}問</span>${engine.state.dailyGoal === n ? icon('check') : ''}</button>`).join('')}`,
  );
}
function exportAccount() {
  try {
    const blob = new Blob([JSON.stringify(engine.exportData(), null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `gtest-learning-${today()}.json`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toast('学習記録を書き出しました');
  } catch (error) {
    toast(error.message || '書き出しに失敗しました');
  }
}
async function importAccount(file) {
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) {
    toast('10MB以下のJSONファイルを選択してください');
    return;
  }
  try {
    const raw = await file.text();
    const preview = createEngine({ meta: engine.meta, questions: engine.bank }, { storage: null });
    const result = preview.importData(raw);
    pendingImport = raw;
    showSheet(
      '学習記録を読み込みますか',
      `<p>
          ${escapeHtml(file.name)}<br />回答${result.answers}件、保存した問題${result.bookmarks}件を読み込みます。現在の記録を置き換えるため、必要な記録は先に書き出してください。
        </p>
        ${primaryButton('現在の記録を書き出す', 'export', 'outline')}${primaryButton('このバックアップを読み込む', 'confirm-import')}<button
          class="secondary-button"
          data-action="close-sheet"
        >
          キャンセル
        </button>`,
    );
  } catch (error) {
    toast(error.message || 'ファイルを読み込めませんでした');
  } finally {
    document.querySelector('#import-file').value = '';
  }
}
function about() {
  showSheet(
    '問題データと出典',
    `<p>G検定向けのAI生成練習問題です。JDLAの公式試験問題ではありません。</p>
      <p>
        出題可能：${engine.bank.length}問・${engine.chapterCount}章<br />元データ：${sourceCount}問<br />解説の選択肢参照を確認中：${heldCount}問
      </p>
      <p>
        解説内の番号・記号と選択肢の対応を確認できない問題は出題を保留しています。内容全体の正確性を保証するものではありません。法律分野などの出典は回答後の「出典・根拠を確認」から開けます。
      </p>
      <p>
        学習にはAPIキーや追加のAI課金は必要ありません。初回の読み込みとキャッシュ保存が完了すると、通信できないときも学習できます。
      </p>`,
  );
}
// Load local app assets; the learning flow does not call a backend or AI service.
async function bootstrap({ refresh = false } = {}) {
  if (booting) return;
  booting = true;
  try {
    const [response, holdResponse] = await Promise.all([
      fetch(STATIC_BANK_URL, { cache: 'no-store' }),
      fetch(HOLD_URL, { cache: 'no-store' }),
    ]);
    if (!response.ok || !holdResponse.ok) throw new Error('問題ファイルを取得できませんでした。');
    const [payload, holds] = await Promise.all([response.json(), holdResponse.json()]);
    const partition = partitionQuestions(payload, holds);
    sourceCount = partition.sourceCount;
    heldCount = partition.heldCount;
    const next = createEngine({ ...payload, questions: partition.questions });
    if (engine?.persistenceError) next.importData(engine.exportData());
    if (!next.bank.length) throw new Error('出題できる問題が見つかりません。');
    engine = next;
    session = engine.getSession();
    if (refresh) {
      view = 'settings';
      toast('問題データを更新しました');
    } else {
      view = 'home';
    }
    render();
  } catch (error) {
    if (engine) {
      toast('更新できませんでした。読み込み済みの問題で学習できます。');
    } else {
      screen.innerHTML = `<section class="page">
        <div class="empty-state">
          ${icon('inbox')}
          <h2>問題を読み込めませんでした</h2>
          <p>初回はインターネット接続が必要です。<br />接続を確認して、もう一度お試しください。</p>
          ${primaryButton('もう一度読み込む', 'retry-load')}
          <p class="error-detail">${escapeHtml(error.message)}</p>
        </div>
      </section>`;
    }
  } finally {
    booting = false;
  }
}
// One delegated handler keeps rerendered screens free of per-row listeners.
app.addEventListener('click', (e) => {
  const target = e.target.closest('[data-action]');
  if (!target || target.disabled) return;
  const raw = target.dataset.action,
    split = raw.indexOf(':'),
    action = split < 0 ? raw : raw.slice(0, split),
    value = split < 0 ? '' : decodeURIComponent(raw.slice(split + 1));
  try {
    if (action === 'retry-load') {
      bootstrap();
      return;
    }
    if (!engine) return;
    if (['home', 'review', 'search', 'settings', 'history'].includes(action)) {
      navigate(action);
      return;
    }
    switch (action) {
      case 'goal':
        goalSheet();
        break;
      case 'set-goal':
        engine.setDailyGoal(Number(value));
        closeSheet();
        render();
        toast(`1日の目標を${value}問にしました`);
        break;
      case 'study':
      case 'start':
        startDaily();
        break;
      case 'extra-study':
        startSession({ mode: 'daily', limit: 10 });
        break;
      case 'resume':
        resume();
        break;
      case 'replace-session':
        if (pendingStart) startSession(pendingStart, true);
        break;
      case 'pause':
        engine.saveSession(session);
        navigate('home');
        toast(session.answers.length ? '続きから再開できます' : '学習を終了しました');
        break;
      case 'answer':
        submitAnswer(Number(value));
        break;
      case 'next':
        nextQuestion();
        break;
      case 'bookmark': {
        const added = engine.toggleBookmark(value);
        render({ resetScroll: false });
        toast(added ? 'あとで復習に保存しました' : '保存を解除しました');
        break;
      }
      case 'bookmarks':
        reviewFilter = 'bookmarked';
        navigate('review');
        break;
      case 'review-filter':
        reviewFilter = value;
        navigate('review');
        break;
      case 'start-review': {
        const rows = reviewQuestions();
        startSession({
          mode: reviewFilter === 'bookmarked' ? 'bookmarked' : 'review',
          limit: 10,
          ids: rows.map((q) => q.id),
        });
        break;
      }
      case 'chapter':
        selectedChapter = value;
        navigate('chapter');
        break;
      case 'start-chapter':
        startSession({ mode: 'chapter', chapter: selectedChapter, limit: 10 });
        break;
      case 'open':
        startSession({ mode: 'daily', ids: [value], limit: 1 });
        break;
      case 'inspect-history':
        inspectRecord = engine.getHistory()[Number(value)] || null;
        if (inspectRecord) {
          inspectId = inspectRecord.question_id;
          inspectBack = 'history';
          navigate('inspect');
        }
        break;
      case 'inspect':
        inspectId = value;
        inspectRecord =
          view === 'summary'
            ? resultSession?.answers.find((a) => a.questionId === value) || null
            : null;
        inspectBack = view;
        navigate('inspect');
        break;
      case 'back':
        navigate(view === 'inspect' ? inspectBack : view === 'chapter' ? 'search' : 'home');
        break;
      case 'more-search':
        searchLimit += 40;
        renderSearchResults();
        break;
      case 'retry-wrong': {
        const ids = resultSession.answers
          .filter((a) => a.choiceIndex !== engine.getQuestion(a.questionId)?.correct_index)
          .map((a) => a.questionId);
        startSession({ mode: 'daily', ids, limit: ids.length }, true);
        break;
      }
      case 'close-sheet':
        closeSheet();
        break;
      case 'export':
        exportAccount();
        break;
      case 'import':
        document.querySelector('#import-file').click();
        break;
      case 'confirm-import':
        if (pendingImport) {
          const result = engine.importData(pendingImport);
          pendingImport = null;
          session = engine.getSession();
          closeSheet();
          navigate('home');
          toast(`${result.answers}件の学習記録を読み込みました`);
        }
        break;
      case 'refresh-bank':
        bootstrap({ refresh: true });
        break;
      case 'install':
        showSheet(
          'ホーム画面に追加',
          `<p>
              iPhoneではSafariでこのアプリを開き、共有ボタンから「ホーム画面に追加」を選択してください。
            </p>
            <p>
              ホーム画面のGtestアイコンから、すぐに学習を始められます。初回はオンラインで問題を読み込んでください。
            </p>`,
        );
        break;
      case 'about':
        about();
        break;
    }
  } catch (error) {
    toast(error.message || '操作を完了できませんでした');
  }
});
document
  .querySelector('#import-file')
  .addEventListener('change', (e) => importAccount(e.target.files?.[0]));
document.addEventListener('keydown', (e) => {
  if (overlayRoot.childElementCount) {
    if (e.key === 'Escape') {
      e.preventDefault();
      closeSheet();
    }
    if (e.key === 'Tab') {
      const items = [...overlayRoot.querySelectorAll('button:not(:disabled),a[href]')];
      if (!items.length) return;
      const first = items[0],
        last = items.at(-1);
      if (
        e.shiftKey &&
        (document.activeElement === first || !items.includes(document.activeElement))
      ) {
        e.preventDefault();
        last.focus();
      } else if (
        !e.shiftKey &&
        (document.activeElement === last || !items.includes(document.activeElement))
      ) {
        e.preventDefault();
        first.focus();
      }
    }
    return;
  }
  if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) return;
  if (e.key === 'Enter' && /BUTTON|A/.test(document.activeElement?.tagName)) return;
  if (view === 'quiz') {
    if (['1', '2', '3', '4'].includes(e.key)) {
      e.preventDefault();
      submitAnswer(Number(e.key) - 1);
    } else if (e.key === 'Enter' && recordFor(currentQuestion() || {})) {
      e.preventDefault();
      nextQuestion();
    } else if (e.key === 'Escape') {
      engine.saveSession(session);
      navigate('home');
    }
  }
});
window.addEventListener('online', () => {
  if (view === 'settings') render();
  toast('オンラインになりました');
});
window.addEventListener('offline', () => {
  if (view === 'settings') render();
  toast('読み込み済みの問題で学習を続けられます');
});
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('./service-worker.js').catch(() => {
    toast('オフライン保存を利用できません。オンラインで学習できます。');
  });
}
bootstrap();
