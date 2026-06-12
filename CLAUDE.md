# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Conversation Guidelines
- 常に日本語で会話する
- 技術的な説明も日本語で行う
- コード内のコメントは日本語で記述
- エラーメッセージの解説は日本語で
- README.mdなどのドキュメントも日本語で作成

## 出力ルール
- 体言止め・短文で応答。敬語不要
- クッション言葉・ぼかし表現を使わない
- 「することができる」→「できる」のように冗長表現を圧縮
- 技術的正確さは維持。省略するのは装飾のみ

## Commands

```bash
# セットアップ（venv は既に存在）
venv\Scripts\activate
pip install -r requirements.txt

# 実行
python main.py                                # 通常実行（5記事生成 + WP下書き投稿）
python main.py --dry-run                      # 投稿せず記事内容を確認
python main.py --topic "任意のトピック"        # テーマを直接指定
python main.py --count 3                      # 生成記事数を変更（デフォルト: 5）
python main.py --dry-run --topic "Arsenal transfer"

# 各モジュール単体実行（デバッグ用）
python topic_finder.py
python researcher.py "Manchester City latest"
python fetcher.py https://www.bbc.com/sport/football/premier-league
python synthesizer.py          # スタブデータで動作確認
python publisher.py            # テスト記事を下書き投稿
python image_fetcher.py "Bruno Fernandes Manchester United"
python score_updater.py        # スコアティッカーを手動更新
```

## Architecture

メインパイプラインは5段階。`main.py` がこれらを直列に呼び出す。

```
topic_finder → researcher → fetcher → synthesizer → publisher
    ↓              ↓           ↓           ↓             ↓
 Topic[]      SearchResult[] FetchedArticle[] GeneratedArticle  PublishResult
```

**データフロー：**
1. `topic_finder.py` — RSS フィード（BBC/Sky Sports/Guardian等）または Nitter 経由 Twitter アカウントからトピック取得。`mode: rss` がデフォルト。`trending`（Tavily検索）、`reddit`（PRAW）、`fixed`（設定ファイル固定リスト）にフォールバックする。Man United 関連は `man_united_boost` 倍のスコア補正。
2. `researcher.py` — Tavily Search API で `include_domains` をホワイトリストに限定して検索。同一ドメイン最大2件に制限。
3. `fetcher.py` — `https://r.jina.ai/{URL}` に GET して Markdown 本文を取得。レスポンスから `Title:` / `Markdown Content:` ヘッダーを分離。
4. `synthesizer.py` — `max_context_words`（デフォルト500語）を上限にソースを切り詰めてClaudeに渡す。`cache_control: ephemeral` でシステムプロンプトをキャッシュ。出力側の引用は `max_quote_words`（100語）で著作権配慮。
5. `publisher.py` — Markdown を独自の `markdown_to_html()` で HTML 変換後、WordPress REST API (`/wp-json/wp/v2/posts`) に Basic認証（アプリケーションパスワード）で POST。

**並列パイプライン：** `main.py` の1回の実行で3本のパイプラインが順次実行される。
- **メイン**（PL全般・Man United）: `find_topics()` → 通常パイプライン
- **移籍**（Man United以外）: `find_topics_transfers()` → `force_category="transfers"`
- **欧州**（PL以外の欧州リーグ）: `find_topics_europe()` → `force_category="europe"`
- **試合分析**: `match_analyzer.py` が直近5日間の完了試合を football-data.org から取得して戦術分析記事を生成

**付属モジュール：**
- `image_fetcher.py` — アイキャッチ画像（横長）と記事内選手写真（縦長）を Wikimedia Commons から取得。見つからない場合は Pexels にフォールバック。
- `score_updater.py` — football-data.org API から PL 最新スコア・順位表・次節日程・4大リーグデータを取得し WordPress ACF オプションを更新。1回の実行開始時に自動実行（失敗しても継続）。

**重複防止：** `processed.db`（SQLite）がトピックハッシュと試合IDを記録し、再実行時にスキップ。

## Key Constraints

- **著作権：** `synthesizer.py:truncate_to_words()` による `max_quote_words`（デフォルト100語）制限を絶対に緩めない。AI入力用の `max_context_words`（500語）とは別管理。
- **下書き固定：** `config.yaml: wordpress.status` のデフォルトは `draft`。自動公開させない。
- **ホワイトリスト：** `researcher.py:is_whitelisted()` が URL を検証。`config.yaml: sources.whitelist` でパス単位まで制御（例: `bbc.com/sport` は `bbc.com/news` を除外）。
- **カテゴリ自動判定：** `main.py:determine_category_slug()` がトピック文字列のキーワードから カテゴリスラッグ（`transfers` / `europe` / `data` / `tactics` / `match-reviews` / `united`）を決定。移籍キーワードが最優先。

## Configuration

`config.yaml` を直接編集して設定変更。`.env` は gitignore 対象。

| 設定 | 場所 | 用途 |
|------|------|------|
| APIキー | `.env` | `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `WP_*`, `REDDIT_*`, `FOOTBALL_DATA_API_KEY`, `PEXELS_API_KEY` |
| ソースホワイトリスト | `config.yaml: sources.whitelist` | ドメイン+パス単位で制御 |
| トピックモード | `config.yaml: topic.mode` | `rss`（デフォルト）/ `trending` / `reddit` / `fixed` |
| 引用語数上限 | `config.yaml: search.max_quote_words` | デフォルト100（著作権・変更禁止） |
| AI入力語数上限 | `config.yaml: search.max_context_words` | デフォルト500（記事品質） |
| WP投稿ステータス | `config.yaml: wordpress.status` | `draft` 固定推奨 |
| カテゴリID | `config.yaml: wordpress.category_ids` | スラッグ→IDのマッピング |
| SSL無効化 | `.env: SSL_VERIFY=false` | 企業プロキシ環境での回避策 |

## Model

`synthesizer.py` と `match_analyzer.py` の `MODEL = "claude-sonnet-4-6"` がハードコードされている。変更する場合はここを直接編集。
