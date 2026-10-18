/** Shared markup and formatting. All user and question text is HTML-escaped. */
const paths = {
  home: '<path d="m3 10 9-7 9 7v11h-6v-7H9v7H3z" fill="currentColor" stroke="none"/>',
  inbox:
    '<path d="M5 4h14l3 15a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2L5 4Z"/><path d="M3 15h5l2 3h4l2-3h5v4l-1 2H4l-2-2z" fill="currentColor" stroke="none"/>',
  create:
    '<path d="M13 4H6a3 3 0 0 0-3 3v11a3 3 0 0 0 3 3h11a3 3 0 0 0 3-3v-6M10 14l1-4L20 1l3 3-9 9-4 1Z"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.8"/><path d="m16 16 5 5"/>',
  settings:
    '<path d="m10 2-.6 3-2 .8L5 4.2 2.4 7l1.7 2.4-.7 2L.5 12v3l3 .6.8 1.8L2.7 20l2.7 2.5 2.4-1.7 2.1.8.6 3h3l.7-3 1.8-.8 2.6 1.7 2.5-2.7-1.8-2.4.8-2 3-.6v-3l-3-.7-.7-1.9 1.6-2.6-2.6-2.5-2.4 1.8-2.1-.8-.6-3H10Z" transform="translate(1 -1) scale(.9)" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="3.8" fill="var(--bg)" stroke="none"/>',
  filter: '<path d="M3 6h8m4 0h6M3 12h3m4 0h11M3 18h11m4 0h3M11 3v6M6 9v6m8 0v6"/>',
  back: '<path d="m11 5-7 7 7 7M4 12h17"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 6v7h5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  share: '<path d="M12 15V2m-4 4 4-4 4 4M6 10H3v11h18V10h-3"/>',
  focus:
    '<path d="M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3M3 16v3a2 2 0 0 0 2 2h3m8 0h3a2 2 0 0 0 2-2v-3"/><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/>',
  document: '<path d="M5 2h9l5 6v14H5zM14 2v6h5"/>',
  phone: '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M10 5h4"/>',
  list: '<path d="M9 5h12M9 12h12M9 19h12"/><circle cx="3" cy="5" r="1"/><circle cx="3" cy="12" r="1"/><circle cx="3" cy="19" r="1"/>',
  bookmark: '<path d="M6 3h12v19l-6-4-6 4V3Z"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
  chart: '<path d="M4 20V10m8 10V4m8 16v-7" stroke-width="3"/>',
  refresh: '<path d="M21 10a9 9 0 1 0-1 7M21 3v7h-7"/>',
};
const icon = (name, style = '') =>
  `<svg
    class="icon"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    ${style ? `style="${style}"` : ''}
  >
    ${paths[name] || paths.document}
  </svg>`;
const escapeHtml = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
const encodeId = (value) => encodeURIComponent(String(value));
const formatPercent = (value) => `${Math.round((value || 0) * 100)}%`;
const dateKey = (value) =>
  new Date(new Date(value).getTime() + 9 * 3600000).toISOString().slice(0, 10);
const today = () => dateKey(new Date());
// Reuse formatters when rendering many history rows instead of allocating one per row.
const shortDateFormatter = new Intl.DateTimeFormat('ja-JP', {
  month: 'numeric',
  day: 'numeric',
  timeZone: 'Asia/Tokyo',
});
const fullDateFormatter = new Intl.DateTimeFormat('ja-JP', {
  month: 'long',
  day: 'numeric',
  weekday: 'short',
  timeZone: 'Asia/Tokyo',
});
const timeFormatter = new Intl.DateTimeFormat('ja-JP', {
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'Asia/Tokyo',
});
const shortDate = (value) => shortDateFormatter.format(new Date(value));
const fullDate = (value) => fullDateFormatter.format(new Date(value));
const timeLabel = (value) => timeFormatter.format(new Date(value));
const dueLabel = (value) => (value ? `${shortDate(value)} ${timeLabel(value)}` : '未設定');
function iconButton(name, label, action, extra = '') {
  return `<button
    class="icon-button ${escapeHtml(extra)}"
    aria-label="${escapeHtml(label)}"
    data-action="${escapeHtml(action)}"
  >
    ${icon(name)}
  </button>`;
}
function primaryButton(text, action, kind = '') {
  return `<button class="primary ${escapeHtml(kind)}" data-action="${escapeHtml(action)}">
    ${escapeHtml(text)}${icon('chevron')}
  </button>`;
}
function listRow({ name, title, sub, action, count = '', tone = '', dot = false }) {
  return `<button class="list-row" data-action="${escapeHtml(action)}">
    <span class="row-icon ${escapeHtml(tone)}">${icon(name)}</span
    ><span class="row-copy"
      ><span class="row-title">${dot ? '<i class="status-dot"></i>' : ''}${escapeHtml(title)}</span
      ><span class="row-subtitle">${escapeHtml(sub)}</span></span
    ><span class="row-end">${escapeHtml(count)}${icon('chevron')}</span>
  </button>`;
}
function pageHeader(
  title,
  {
    back = false,
    action = 'goal',
    actionIcon = 'filter',
    actionLabel = '1日の目標を変更',
    eyebrow = 'Gtest',
  } = {},
) {
  return `<header class="page-header">
    <div class="eyebrow-line">
      <div class="eyebrow">
        ${back ? iconButton('back', '戻る', 'back') : '<span class="brand-dot" aria-hidden="true"></span>'}${escapeHtml(eyebrow)}
      </div>
      ${action ? iconButton(actionIcon, actionLabel, action) : ''}
    </div>
    <h1 class="page-title">${escapeHtml(title)}</h1>
  </header>`;
}
function settingsRow(name, label, action, value = '') {
  return `<button class="settings-row" data-action="${escapeHtml(action)}">
    ${icon(name)}<span class="grow">${escapeHtml(label)}</span
    ><span class="value">${escapeHtml(value)}</span>${icon('chevron', 'width:13px;height:13px')}
  </button>`;
}

export {
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
};
