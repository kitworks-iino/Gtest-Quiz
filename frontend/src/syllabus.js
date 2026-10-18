import { escapeHtml, icon, pageHeader } from './ui.js';

export const SYLLABUS_SOURCE = 'https://www.jdla.org/certificate/general/#general_No03';
const REVIEWED_CONTENT_HASH = '1c77a68ada0c653c7412f637d7597aa403c22fa1576faae30cc51df0932e7916';

// Editorial learning notes are only shown for the official document reviewed here.
const guide = {
  '人工知能とは': {
    label: 'AIの定義と限界',
    summary: 'AIの意味と限界を、身近な例から理解する分野です。',
    points: ['AI・機械学習・深層学習の関係を整理する', 'フレーム問題やチューリングテストの論点を説明する'],
  },
  '人工知能をめぐる動向': {
    label: '発展の流れ',
    summary: '探索や知識表現から深層学習まで、発展の流れを学びます。',
    points: ['探索・推論とエキスパートシステムの違いを押さえる', 'ルールで作る方法とデータから学ぶ方法を比較する'],
  },
  '機械学習の概要': {
    label: '手法の選択と評価',
    summary: '課題に合う学習方法を選び、結果を正しく評価します。',
    points: ['教師あり・教師なし・強化学習を目的で使い分ける', '過学習を防ぐ検証と、適合率・再現率などの評価指標を整理する'],
  },
  'ディープラーニングの概要': {
    label: '学習の仕組み',
    summary: 'ニューラルネットワークが学習する仕組みを理解します。',
    points: ['活性化関数・誤差関数・誤差逆伝播の役割をつなげる', '正則化・最適化・学習率が学習に与える影響を押さえる'],
  },
  'ディープラーニングの要素技術': {
    label: '各層の役割',
    summary: '各層や仕組みが、どのようなデータに向くかを学びます。',
    points: ['畳み込み・回帰結合・Attentionの役割を比較する', '正規化・スキップ結合・データ拡張の目的を整理する'],
  },
  'ディープラーニングの応用例': {
    label: '用途と技術の対応',
    summary: '画像・言語・音声や生成AIを、用途と技術で結び付けます。',
    points: ['タスクと代表的なモデル、生成の仕組みを対応付ける', '転移学習・マルチモーダル・解釈性・軽量化の使い所を押さえる'],
  },
  'AIの社会実装に向けて': {
    label: '導入から運用まで',
    summary: '課題設定から開発・運用まで、AI導入の流れを学びます。',
    points: ['PoCから運用監視まで、段階ごとの判断点を整理する', 'データの取得・加工・共有とリーケージに注意する'],
  },
  'AIに必要な数理・統計知識': {
    label: '数学・統計の基礎',
    summary: '学習や評価の土台となる数学・統計を確認します。',
    points: ['微分と最適化のつながりを理解する', '確率・分散・相関などを簡単な例で計算する'],
  },
  'AIに関する法律と契約': {
    label: '権利と責任',
    summary: 'データ・生成物・開発契約の扱いを事例で考えます。',
    points: ['個人情報・著作権・特許・営業秘密の違いを整理する', '開発委託とサービス利用で、権利・利用条件・責任を確認する'],
  },
  'AI倫理・AIガバナンス': {
    label: 'リスクと管理',
    summary: 'AIの社会的なリスクと、継続して管理する方法を学びます。',
    points: ['公平性・プライバシー・安全性・透明性を具体例で考える', '人の関与・監査・監視を運用に組み込む理由を押さえる'],
  },
};

function officialUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && ['www.jdla.org', 'jdla.org'].includes(url.hostname)
      && !url.username && !url.password && !url.port;
  } catch (_error) {
    return false;
  }
}
export function validateSyllabus(data) {
  const validText = (value) => typeof value === 'string' && value.trim().length > 0 && value.length < 2000;
  if (!data || data.schema_version !== 'gtest_official_syllabus_v1'
      || !/^\d{4}-\d\d-\d\dT/.test(data.checked_at || '')
      || !Number.isFinite(Date.parse(data.checked_at))
      || Date.parse(data.checked_at) > Date.now() + 300000
      || !validText(data.revision)
      || !/^[a-f0-9]{64}$/.test(data.content_hash || '')
      || !/^[a-f0-9]{64}$/.test(data.document_sha256 || '')
      || ![data.source_url, data.download_page_url, data.document_url].every(officialUrl)
      || !Array.isArray(data.sections) || !data.sections.length || data.sections.length > 100
      || !data.sections.every((section) => section && validText(section.id) && validText(section.title)
        && validText(section.category) && Array.isArray(section.topics) && section.topics.length > 0
        && section.topics.length <= 200 && section.topics.every(validText))
      || new Set(data.sections.map((section) => section.id)).size !== data.sections.length) {
    throw new Error('シラバスの掲載情報を確認できませんでした');
  }
  return data;
}
const checkedFormatter = new Intl.DateTimeFormat('ja-JP', {
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  hourCycle: 'h23', timeZone: 'Asia/Tokyo',
});
function sourceLink(url, label, className = '') {
  return `<a class="syllabus-link ${className}" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}${icon('share')}</a>`;
}

export function syllabusMarkup(data, { loading = false, failed = false, offline = false, now = Date.now() } = {}) {
  const header = pageHeader('公式シラバス', {
    back: true, eyebrow: '学習ガイド', action: loading ? null : 'refresh-syllabus',
    actionIcon: 'refresh', actionLabel: '掲載情報を再読み込み',
  });
  if (!data) {
    return `<section class="page syllabus-page">${header}
      <div class="syllabus-intro" role="status">
        <p>${loading ? '公式シラバスの掲載情報を読み込んでいます。' : '掲載情報を読み込めませんでした。通信できるときに再読み込みするか、公式ページでご確認ください。'}</p>
        ${sourceLink(SYLLABUS_SOURCE, '公式の最新掲載ページを開く')}
      </div></section>`;
  }
  validateSyllabus(data);
  const reviewed = data.content_hash === REVIEWED_CONTENT_HASH;
  const stale = now - Date.parse(data.checked_at) > 48 * 3600000;
  const topicCount = data.sections.reduce((sum, section) => sum + section.topics.length, 0);
  const categories = [...new Set(data.sections.map((section) => section.category))];
  return `<section class="page syllabus-page">${header}
    <div class="syllabus-intro">
      <div class="syllabus-kicker">JDLA 公開資料${reviewed ? ' · 第1.4版' : ''}</div>
      <p class="syllabus-lead">出題範囲を、ひと目で。</p>
      <p>何を学び、何を説明できるようにするか。分野ごとに確認できます。</p>
      <div class="syllabus-counts"><span><strong>${data.sections.length}</strong>分野</span><span><strong>${topicCount}</strong>項目</span></div>
      <div class="syllabus-source-meta">
        <span>${escapeHtml(data.revision)}</span>
        <span>公式確認：${escapeHtml(checkedFormatter.format(new Date(data.checked_at)))}（日本時間）</span>
        ${data.source_updated_at ? `<span>公式掲載ページの更新日：${escapeHtml(data.source_updated_at)}</span>` : ''}
      </div>
      ${loading ? '<p class="syllabus-status" role="status">掲載情報を再読み込み中…</p>' : ''}
      ${offline || failed || stale ? `<p class="syllabus-status" role="status">${offline ? 'オフラインのため、保存済みの内容を表示しています。' : failed ? '再読み込みできなかったため、前回取得した内容を表示しています。' : '公式確認から2日以上経過しています。'} 現在の内容は公式原文でもご確認ください。</p>` : ''}
      ${sourceLink(data.document_url, '公式原文を読む（PDF）', 'primary')}
      ${sourceLink(SYLLABUS_SOURCE, '公式の最新掲載ページを開く')}
      <p class="syllabus-caption">公式資料は毎日確認します。公式の更新からアプリへの反映には時間差があります。原文は別タブで開きます。</p>
    </div>
    ${reviewed ? `<aside class="syllabus-revision">
      <span class="syllabus-kicker">今回の版で変わったところ</span>
      <p>自然言語処理のキーワードから「ChatGPT」「GPT-n」が削除され、「破壊的忘却」は「破滅的忘却」に修正されました。</p>
      <span class="syllabus-caption">第1.4版（2026年5月11日）・原文の改訂履歴より</span>
    </aside>` : `<p class="syllabus-status syllabus-review-notice">公式資料が更新されています。各分野の学習ポイントは確認中です。下記の出題項目と公式原文をご確認ください。</p>`}
    <div class="syllabus-section-intro">
      <h2>分野別の学習ポイント</h2>
      <p>分野をタップして展開します。概要・ポイントはアプリ独自の整理で、公式の配点や頻出順位を示すものではありません。</p>
    </div>
    ${categories.map((category) => `<h2 class="section-title syllabus-category">${escapeHtml(category)}</h2>
      ${data.sections.filter((section) => section.category === category).map((section) => {
        const index = data.sections.indexOf(section);
        const note = reviewed ? guide[section.title] : null;
        return `<details class="syllabus-section" ${index === 0 ? 'open' : ''}>
          <summary><span class="syllabus-number">${String(index + 1).padStart(2, '0')}</span>
            <span class="row-copy"><span class="row-title">${escapeHtml(section.title)}</span>
              <span class="row-subtitle">${section.topics.length}項目${note ? ` · ${escapeHtml(note.label)}` : ''}</span></span>${icon('chevron')}</summary>
          <div class="syllabus-detail">
            ${note ? `<p class="syllabus-summary">${escapeHtml(note.summary)}</p><h3>押さえるポイント</h3>
              <ul class="syllabus-points">${note.points.map((point) => `<li>${escapeHtml(point)}</li>`).join('')}</ul>` : ''}
            <h3>公式の出題項目</h3><ul class="syllabus-topics">${section.topics.map((topic) => `<li>${escapeHtml(topic)}</li>`).join('')}</ul>
          </div></details>`;
      }).join('')}`).join('')}
    <footer class="syllabus-footnote">${sourceLink(data.download_page_url, '資料の配布ページを確認')}
      <p>この画面は出題範囲の確認用です。アプリ内の練習問題すべてが最新シラバスへ対応済みであることを示すものではありません。</p>
    </footer>
  </section>`;
}
