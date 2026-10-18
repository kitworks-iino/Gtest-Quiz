# Gtest

G検定を1問ずつ学ぶ、モバイル向けの静的PWAです。

**本番:** https://kitworks-iino.github.io/Gtest-Quiz/

- 今日の学習、目標設定、正誤と解説、章別演習、問題検索
- 間違えた問題・復習時期になった問題・ブックマークの復習
- 学習記録、途中再開、JSONによる履歴エクスポート／インポート
- 初回のオンライン読み込み・キャッシュ保存後はオフラインでも学習可能

iPhoneではSafariで本番URLを開き、共有メニューの「ホーム画面に追加」で利用できます。ログインやAPIキーは不要です。

## 構成

| 場所 | 役割 |
| --- | --- |
| `frontend/src/` | 公開アプリ。画面、学習エンジン、出題保留、オフラインキャッシュ |
| `bank/question_bank.jsonl` | 問題データの正本 |
| `bank/meta.json` | 章定義と生成記録 |
| `bank/generated_review_queue.jsonl` | 要確認・不採用の生成候補 |
| `bank/question_provenance.jsonl` | 問題の生成根拠・履歴 |
| `gtest_quiz/` | Geminiによる問題生成と品質検証 |
| `tools/` | 補充、検証、配信用データ生成、公開判定 |
| `tests/` | 現行PWAと問題生成の回帰テスト |

学習はブラウザだけで動きます。PythonとGemini APIは問題補充にだけ使用します。学習中のLLM通信・外部DB・外部解析SDKはありません。

## データと費用

学習履歴・目標・ブックマーク・中断状態は、そのURLを開いたブラウザの `localStorage` に保存します。端末間・本番と確認用サイト間で自動同期しません。アプリの設定から定期的にエクスポートしてください。保存失敗は画面に表示します。

問題が追加・更新されても履歴を自動削除しません。保留中の問題に対する既存履歴は保存し、現在の学習指標からは除外します。

学習操作にはAI利用料が発生しません。問題補充のGemini API利用は別です。Google AI Pro契約だけを根拠にAPI無料と扱わず、利用するGoogleプロジェクトの課金設定で管理します。キーはGitHub ActionsのSecretsまたはローカルの `.env` に置き、配信ファイルには含めません。

## 問題の品質

画面の収録数は、元データから出題保留を除いた利用可能数です。保留対象は `frontend/src/question-review-holds.json` と `content-guard.js` で管理します。保留を解除するには解説と選択肢の対応を確認します。機械検証だけで全内容の事実確認済みとは扱いません。

問題の正解・解説はオフライン採点のため配信データに含みます。本アプリは個人学習用です。

## 開発・検証

Python **3.11** と Node.js **22** を使用します。テストにAPIキーは不要です。

```bash
python -m pip install -r requirements-dev.txt
python tools/build_static_pwa_assets.py
python tools/validate_question_bank.py
node --test tests/frontend/*.test.mjs
python -m pytest --cov=gtest_quiz --cov-report=term-missing --cov-fail-under=70
READINESS_PROFILE=bootstrap python tools/release_readiness.py
```

CI release gate は上記の全検査です。Python coverage の対象は現行の `gtest_quiz` 全体で、最低70%を維持します。公開判定は配信されるJavaScriptを実行して採点・復習・保存・再開・復元を検証し、**実際に出題できる問題数**で判定します。

`bootstrap` は現在の運用確認用です。`complete` は550問・55章すべて10問以上などの品質基準を満たす必要があり、保留問題を含めて達成扱いにしません。引数なしの公開判定は `complete` を使用します。

## 公開と運用

GitHub Pagesが `frontend/src/` を配信します。mainの関連ファイル更新、または問題補充の正常終了で再公開されます。問題補充は毎日 `00:00 UTC / 09:00 JST` の設定です。実際の開始時刻はGitHub側で遅れる場合があります。

操作、品質基準、サービス管理先、復旧手順は [保守手順](docs/maintenance.md) にまとめています。
