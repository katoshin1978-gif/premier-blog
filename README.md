# Premier Blog - プレミアリーグ自動投稿システム

海外複数サイトからプレミアリーグ情報を収集し、日本語記事としてWordPressに下書き投稿する自動化システムです。

## システム構成

```
premier-blog/
├── config.yaml          # 設定ファイル（ホワイトリスト、テーマなど）
├── .env                 # 秘密情報（APIキー等）
├── topic_finder.py      # テーマ抽出（Reddit or 固定リスト）
├── researcher.py        # Tavily Search API で記事検索
├── fetcher.py           # Jina Reader で本文取得
├── synthesizer.py       # Claude API で日本語記事生成
├── publisher.py         # WordPress REST API で下書き投稿
├── main.py              # オーケストレーション
├── processed.db         # 重複防止用SQLite（自動生成）
└── requirements.txt
```

## セットアップ

### 1. 依存パッケージのインストール

```bash
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # Mac/Linux

pip install -r requirements.txt
```

### 2. 設定ファイルの準備

```bash
cp config.yaml.example config.yaml
cp .env.example .env
```

`config.yaml` を編集してWordPressのURLとカテゴリIDを設定します。

### 3. APIキーの設定

`.env` ファイルに以下を記入します：

| 変数名 | 取得先 |
|--------|--------|
| `ANTHROPIC_API_KEY` | [Anthropic Console](https://console.anthropic.com/) |
| `TAVILY_API_KEY` | [Tavily](https://tavily.com/) |
| `REDDIT_CLIENT_ID` | [Reddit Apps](https://www.reddit.com/prefs/apps) （`mode: reddit` の場合のみ）|
| `REDDIT_CLIENT_SECRET` | 同上 |
| `WP_URL` | WordPressサイトのURL |
| `WP_USERNAME` | WordPressユーザー名 |
| `WP_APP_PASSWORD` | WordPress管理画面 → ユーザー → アプリケーションパスワード |

### 4. WordPress アプリケーションパスワードの発行

1. WordPress管理画面 → **ユーザー** → **プロフィール**
2. 「アプリケーションパスワード」セクションへスクロール
3. 名前（例: `premier-blog`）を入力して「新しいアプリケーションパスワードを追加」
4. 表示されたパスワードを `.env` の `WP_APP_PASSWORD` に設定

## 実行方法

### 通常実行（WordPress に下書き投稿）

```bash
python main.py
```

### ドライラン（投稿せず記事内容を確認）

```bash
python main.py --dry-run
```

### テーマを直接指定

```bash
python main.py --topic "Erling Haaland injury update"
python main.py --topic "Arsenal Champions League" --dry-run
```

## 各モジュールの単体テスト

```bash
# テーマ抽出のみテスト
python topic_finder.py

# 記事検索のみテスト
python researcher.py "Manchester City transfer news"

# 本文取得のみテスト
python fetcher.py https://www.bbc.com/sport/football/premier-league

# WordPress 投稿のみテスト（テスト記事を下書き投稿）
python publisher.py
```

## 設定のカスタマイズ（config.yaml）

### テーマ抽出モードの変更

```yaml
topic:
  mode: fixed     # "reddit" に変更すると r/soccer から自動取得
  fixed_themes:
    - "Premier League latest news"
    - "プレミアリーグ 移籍情報"
```

### ソースホワイトリストの変更

```yaml
sources:
  whitelist:
    - bbc.com/sport
    - theguardian.com/football
    # 追加したいドメインをここに記入
```

### 引用語数の上限変更

```yaml
search:
  max_quote_words: 100    # 著作権配慮のため100語推奨
```

## 著作権について

本システムは以下の著作権配慮を実装しています：

- 各ソースからの引用は最大100語まで
- 原文へのリンクを記事内に必ず明示
- 記事末尾の「情報源」セクションで全ソースをリスト化
- 複数ソースから少量ずつ引用し、単一ソースへの依存を回避

## 自動実行（Windowsタスクスケジューラ）

```powershell
# 毎日 9:00 に自動実行する例
schtasks /create /tn "PremierBlog" /tr "C:\path\to\venv\Scripts\python.exe C:\path\to\main.py" /sc daily /st 09:00
```
