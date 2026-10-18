# 保守手順

## 管理するサービス

| 対象 | 役割・管理先 |
| --- | --- |
| GitHubリポジトリ | [kitworks-iino/Gtest-Quiz](https://github.com/kitworks-iino/Gtest-Quiz)。コード・問題の正本 |
| GitHub Pages | [本番](https://kitworks-iino.github.io/Gtest-Quiz/)。[公開設定](https://github.com/kitworks-iino/Gtest-Quiz/settings/pages) |
| GitHub Actions | [実行履歴](https://github.com/kitworks-iino/Gtest-Quiz/actions)。CI、問題補充、公開、リリース検査 |
| Gemini API | [APIキー](https://aistudio.google.com/api-keys)。キーはGitHubの `GEMINI_API_KEY` Secret、モデルは `GEMINI_MODEL` Variable |

Google AI Proと外部アプリからのGemini API利用は別管理です。APIの費用・上限は該当Googleプロジェクトで確認します。このリポジトリから課金状態は判断できません。

## フロントエンド

- `offline-app.js`: 画面の切替とユーザー操作。
- `ui.js`: アイコン、表示部品、文字列の安全なHTML変換。
- `learning-engine.js`: 採点、復習間隔、出題、履歴保存。DOM・ネットワークに依存しません。
- `content-guard.js`: 保留IDと解説の選択肢位置参照の検出。
- `syllabus.js`: 公式シラバスの概要・学習ポイント・原文リンク。ホームと設定から開きます。
- `styles.css`: アプリの表示。フォント・アイコンも同一サイト内で配信します。
- `service-worker.js`: 画面・問題・保留リストのキャッシュ。

保存キー `gtestQuizOfflineStateV1` とエクスポート形式 `gtest_quiz_offline_export_v1` は既存データとの互換性を維持します。正誤はインポートされた値を信用せず、現在の問題データから再計算します。

復習は期限到来と直近の誤答を優先します。正答は復習間隔を延ばし、誤答は短くします。連続学習・日別記録は日本時間で区切ります。

## 公式シラバス

`Sync Official Syllabus` が毎日09:15（日本時間）にJDLAの公式掲載ページとPDFを確認し、`frontend/src/official-syllabus.json` を更新します。問題生成とは独立して動作し、AI APIや追加サービスを使いません。確認が成功した更新はPagesへ公開します。

取得・構造の検証に失敗した場合は前回データと確認日時を維持します。アプリは掲載情報を開くたびに取得し、オフライン時は保存済みの内容を表示します。公式更新の即時反映は保証せず、確認日時と公式原文へのリンクを常に表示します。

学習ポイントは第1.4版（2026年5月11日）の確認に基づく編集です。PDFを含む公式資料のハッシュが変わった場合は非表示にし、出題項目と原文へのリンクを表示します。再確認後に `syllabus.js` の内容と `REVIEWED_CONTENT_HASH` を一緒に更新してください。アプリの問題バンク全体の最新準拠を意味しません。

## 問題補充

通常の入口は `tools/auto_refill_quality.py` です。

```bash
# 課金するAPIを呼ばず既存問題を検査
python tools/auto_refill_quality.py --mode validate_only
python tools/review_generated_queue.py --summary

# APIキーを使って追加生成（利用するAPIプロジェクトの費用設定に従う）
python tools/auto_refill_quality.py --mode daily --target 5
```

標準モデルは `gemini-3.5-flash` です。workflowの `ENFORCE_GEMINI35=true` では別モデルを拒否し、古いモデルへの暗黙切替は行いません。

処理は章別の不足把握 → スキーマに沿った生成 → 品質検証 → 正解位置の配置 → バッチ検証 → 正本へ保存、の順です。解説は選択肢の内容を説明し、番号やA/B/C/Dだけで参照しません。位置参照を検出した候補は出題に入れません。

既存の100問単位の正解位置分布を考慮して配置します。バッチ全体が分布検査を通らない場合は問題バンクへ追記しません。品質基準は下げません。

検査対象はスキーマ、重複、選択肢、解説長、難易度、レビュー評価、出典、モデル・バンク世代、正解位置分布です。法律・倫理分野には一次資料のURL・名称・版・確認日時・根拠を持たせます。出典メタデータの存在だけで事実確認が完了したとは扱いません。

要確認候補はレビューキューへ残します。確認済み候補の昇格は `tools/review_generated_queue.py --promote <index>` で行い、通常と同じ検査を通します。

手動モードは `daily`、`seed`、`reset_and_seed`、`replace`、`build_to_complete`、`build_to_expanded`、`validate_only` です。通常は `daily` を使用します。置換・再作成モードは明示的な問題整理時に限り、既存の履歴IDとの対応に注意します。

生成だけ成功しても、後続検証が失敗するとmainへ保存されません。Actionsの各ステップと結果JSONを確認してください。テスト・今回の保守検査は実APIを呼び出しません。

## 配信と品質ゲート

```bash
python tools/generate_coverage_report.py
python tools/build_static_pwa_assets.py
python tools/validate_question_bank.py
node --test tests/frontend/*.test.mjs
python -m pytest --cov=gtest_quiz --cov-report=term-missing --cov-fail-under=70
READINESS_PROFILE=bootstrap python tools/release_readiness.py
```

`build_static_pwa_assets.py` は正本からJSONを生成し、内容が同じならファイルを書き換えません。UI・問題・workerのハッシュでキャッシュ名を更新し、アイコン等の欠損はビルドエラーにします。UIファイルを追加した場合は同スクリプトの `SHELL_FILES` とworkerの `SHELL` を更新してください。両者の一致はテストされます。

問題と保留リストはnetwork-firstで取得し、通信障害・HTTPエラー・無効なJSONでは保存済みの有効なデータへ戻ります。新workerのインストールは全資産が揃うまで完了しません。

| 公開判定 | 最低問数 | 最低章数 | 全章の最低問数 |
| --- | ---: | ---: | ---: |
| bootstrap | 1 | 1 | 指定なし |
| alpha | 100 | 20 | 指定なし |
| beta | 275 | 55 | 5 |
| complete | 550 | 55 | 10 |
| expanded | 1000 | 55 | 15 |

難易度・出典・重複・正解位置などの検査も必要です。元データの検査に加え、保留を除いた利用可能数・章別件数・難易度で判定します。`complete` を満たさないことと、既存問題で学習できないことは同義ではありません。

CIは実運用と同じPython3.11で検査します。Pages配信前にもバンク検証とブラウザモジュールのテストを実施します。Pagesの配信Artifactは1日保存です。自動補充成功後は `workflow_run` を受けて最新mainを公開します。

## 復旧

画面更新に不具合があればGitの正常な変更をrevertしてmainへ反映し、Pagesの公開成功を確認します。問題データまで以前に戻す必要がなければ、UI関連ファイルだけを戻します。利用者へブラウザデータの削除を求めると学習履歴が消えるため、通常の復旧手順には含めません。

公開後は本番で開始 → 回答 → 解説 → 次の問題 → 中断 → 再読み込み → 再開を確認します。データの復元は設定のJSONインポートを利用します。
