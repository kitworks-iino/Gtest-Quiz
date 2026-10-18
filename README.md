# Gtest-Quiz

## Current bank status

This section mirrors `bank/coverage_report.json`; run `python tools/generate_coverage_report.py` after changing the bank.

- bank_version: `gemini35_v1`
- model: `gemini-3.5-flash`
- active questions: `919`
- currently available for learning: `497`; `422` are held for revalidation of explanation references to shuffled choice positions
- current readiness: `beta`
- chapter coverage: `55 / 55`
- alpha target: `100` questions
- beta target: `275` questions and all 55 chapters covered
- complete target: `550` questions, all 55 chapters with at least 10 questions
- expanded target: `1000` questions
- current complete progress: question-count target met; `47 / 55` chapters have at least 10 questions

The coverage counts reflect the checked-in source-bank report (2026-10-16), before the UI's review holds. The app excludes IDs in `frontend/src/question-review-holds.json` and displays the usable count. Neither the 919 source questions nor the 497 usable questions imply that the complete readiness gate has passed.

Readiness profiles are `bootstrap`, `alpha`, `beta`, `complete`, and `expanded`. `complete` is the G検定 learning-app completion gate; it must not be weakened just to pass.

G検定向けの完全オフライン静的PWAです。現行の推奨利用形態は `frontend/src/` をGitHub Pagesなどの静的ホスティングで配信し、iPhoneのSafariからホーム画面に追加して使う形です。日常利用ではPCサーバー、ログイン、APIキー、バックエンド起動は不要です。

## Current Product Path

- `frontend/src/`: 現行本番の静的PWA。問題演習、復習スケジュール、学習履歴、エクスポート/インポートをブラウザ内で完結します。`index.html` が `offline-app.js` をES moduleとして読み込み、表示は `styles.css`、採点・保存・出題は `learning-engine.js` が担当します。
- `bank/question_bank.jsonl`: 正本の問題バンク。`tools/build_static_pwa_assets.py` で `frontend/src/question-bank.json` に変換します。
- `gtest_quiz/`: 問題生成、検証、シラバス/メタ情報、共通モデル、品質ロジックのライブラリです。
- `tools/`: PWA資産生成、問題バンク検証、品質補充、release readiness、ベンチマークなどの補助CLIです。
- `backend/`: 将来の同期/API/認証/監査ログ用およびローカル検証用です。現行PWAの日常利用には不要で、公開APIとしてそのまま運用する前提ではありません。
- `app.py` / `.streamlit/`: 旧Streamlit実験UIです。現行本番ではなくlegacy扱いです。
- `tools/legacy/`: 古い生成スクリプトです。通常は使わず、現行の生成ルートは `tools/auto_refill_quality.py` と `gtest_quiz.content_factory` です。

## iPhoneで使う

1. iPhoneのSafariでGitHub Pagesの公開URLを開きます。
2. 共有メニューから「ホーム画面に追加」を選びます。
3. 以後はホーム画面の「Gtest」アイコンから起動します。

```text
https://kitworks-iino.github.io/Gtest-Quiz/
```

初回のオンラインアクセス時に、HTML、JavaScriptの各モジュール、CSS、フォント、manifest、PWAアイコン、問題バンクをService Workerがまとめてキャッシュします。保存完了後はオフラインでも起動・演習できます。`question-bank.json` は毎回network-firstで取得し、新しい有効なJSONを取得できたときにキャッシュを更新します。通信失敗、HTTPエラー、不正なJSONの場合は最後の問題バンクを使い、問題バンクの代わりにHTMLを返しません。

出題保留リストも同時にキャッシュ・更新します。既存の保留IDに加え、`content-guard.js` が解説中の選択肢位置参照を検出した問題を演習から除外します。これは参照不整合の対策であり、解説全体の事実確認を保証するものではありません。日次補充workflowがmainで正常終了すると、Pages workflowが最新mainを取得して再配信します。GitHub Actions自身のpushでは別workflowが起動しないため、補充完了イベントを明示的な配信トリガーにしています。

## 学習画面

ダークテーマのモバイルUIから、日々の演習、章別学習、復習、問題検索、ブックマーク、学習履歴を利用できます。回答後は正誤・解説を表示し、法律・倫理問題では登録済みの一次資料も確認できます。回答履歴に基づく出題と復習間隔の計算は端末内で動作し、毎問のLLM呼び出しや利用料金は発生しません。

## Data and Recovery

- 回答履歴、復習スケジュール、学習ポリシーはブラウザの `localStorage` に保存されます。
- `localStorage` は恒久保存ではありません。SafariのWebサイトデータ削除、端末変更、ブラウザ変更、プライベートブラウズ、iOSのストレージ整理、誤操作で消える可能性があります。
- 定期的にアプリ内の `エクスポート` で学習データをJSONとして退避してください。
- `インポート` で退避データを復元できます。
- インポート時の正誤は `question-bank.json` の `correct_index` から再計算されます。
- 旧版と同じ保存キーを引き継ぎます。問題バンクの世代が変わっても履歴を自動削除せず、現在の問題IDに対応する回答を学習指標へ反映します。
- ブックマーク、1日の目標、途中の学習セッションも端末内に保存します。保存できない環境では保存エラーを表示します。

静的PWAはオフライン採点のため、配信される `question-bank.json` に `correct_index` と `explanation` を含みます。このアプリは個人学習用です。試験実施、採点付き共有テスト、答えを隠す必要がある用途には使えません。

## API Keys

Gemini APIキーは問題生成・補充フローだけで使います。静的PWAで練習するだけなら不要です。APIキーを `index.html` や `offline-app.js` に書いてはいけません。公開PWAでは全員に見えます。

## Question Bank Maintenance

正本の問題バンクを更新したら、静的PWA資産を再生成します。

```bash
python tools/validate_question_bank.py
python tools/build_static_pwa_assets.py
```

`tools/build_static_pwa_assets.py` は `frontend/src/question-bank.json` に `content_hash` を書き込み、同じ問題データならtimestampだけの差分を作りません。Service Workerの `CACHE_NAME` はbank version、問題内容、HTML・CSS・JavaScript・フォント・アイコン・worker本体のハッシュから決まり、UIだけの変更でもオフラインキャッシュが更新されます。日次の資産生成では作成済みのUIやアイコンを上書きしません。

GitHub Actionsの本線は `Auto Refill Question Bank (Quality Pipeline)` です。JST 09:00相当の `0 0 * * *` で毎日1回、`DAILY_TARGET=10` を初期値としてGemini 3.5 Flash世代の問題を追加生成します。daily targetは前回のaccepted/rate limit/API call結果を見て自動調整します。手動実行では `reset_and_seed`、`seed`、`daily`、`replace`、`build_to_complete`、`build_to_expanded` を選べます。legacyの `auto_refill.yml` は無効化済みで、二重起動しません。

手動 `seed` / `build_to_complete` で `accepted=0` になりassertが失敗したrunは、`bank/meta.json` の `last_refill_result` がmainへcommitされない場合があります。その場合の次回target調整はGitHub Actionsログを見て判断します。一方、日次 `daily` でrate limit/quota signal付きの `accepted=0` になった場合はassertを通過でき、`bank/meta.json` がcommitされれば次回の日次target自動調整に利用されます。

```bash
python tools/auto_refill_quality.py
python tools/validate_question_bank.py
python tools/review_generated_queue.py --summary
python tools/build_static_pwa_assets.py
```

モデルは問題生成向けに `gemini-3.5-flash` を既定値にしています。`GEMINI_MODEL` が設定されている場合だけ上書きします。旧モデルへの暗黙fallbackはしません。生成結果はvalidationとreview queue/provenanceを通し、review warningがない候補だけactive bankへ入れます。選択肢は生成後にシャッフルし、`correct_index` を再計算して `choice_shuffle_seed` をprovenanceへ残します。`bank/question_bank.jsonl`、`bank/meta.json`、`bank/generated_review_queue.jsonl`、`bank/question_provenance.jsonl`、`frontend/src/question-bank.json`、`frontend/src/offline-app.js` をcommit対象にします。

現在のactive bank世代は `gemini35_v1` です。問題生成と品質検査はリポジトリ側で実施し、PWAには検査済みの静的問題バンクを配信します。`bank_version` の変更だけを理由に端末の学習履歴を初期化することはありません。

## Quality Gates

CI release gateとして見るべき基準は、以下をすべて通すことです。

```bash
python tools/validate_question_bank.py
python tools/build_static_pwa_assets.py
pytest tests/contracts
node --test tests/frontend/*.test.mjs
pytest tests/test_question_quality.py tests/test_content_factory.py tests/test_refill_pipeline.py tests/integration tests/backend tests/frontend -m "not e2e" --cov=gtest_quiz --cov=backend/app --cov-report=term-missing --cov-fail-under=70
python tools/benchmark_learning_policy.py --compare
python tools/release_readiness.py
pytest tests/e2e -m e2e
```

`tools/release_readiness.py` はPWA資産、問題バンク、answer-key safety、復旧経路、サービスsmoke、ベンチマーク、deployment profileを確認するrubricです。デフォルトのproduction profileでは `PRODUCTION_MIN_QUESTIONS` 未満の問題バンクをrelease readyにしません。Gemini 3.5移行直後と初期seed中だけ `READINESS_PROFILE=bootstrap` を明示してbootstrap readinessとして確認します。coverageそのものはpytest-covのCI gateで保証します。つまり `release_readiness.py` 単体は「全CIの代替」ではなく、CI release gateの一部です。

coverage対象は `.coveragerc` で現行保守対象に絞っています。FastAPI APIルータ、ASGIアダプタ、Streamlit UI、旧生成パイプラインは将来/legacy補助機能としてcoverage gateから外しています。

## Backend Status

FastAPI版とstdlib HTTP版は、将来の同期/API/認証、OpenAPI契約、ローカル検証、移行検討のために残しています。現時点で公開APIとして運用する完成度ではありません。公開する場合は、CORS、レート制限、ログ保護、セッション有効期限、import/export入力制限、監査ログ、本番/開発プロファイル、hosted運用時の脅威モデルを再設計してください。
