"""
試合分析記事生成モジュール
football-data.org から直近の試合データを取得し、
戦術分析に特化した日本語記事を生成する
"""

import os
from datetime import datetime, timedelta, timezone

import anthropic
import httpx
import requests
import yaml
from dotenv import load_dotenv

from fetcher import fetch_articles
from researcher import search_articles
from synthesizer import GeneratedArticle, PLAYER_NAMES_GUIDE, _extract_seo_meta, _extract_title

load_dotenv()

JST = timezone(timedelta(hours=9))
_SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() != "false"
FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
PL_ID = "PL"
MAN_UNITED_ID = 66
MODEL = "claude-sonnet-4-6"


def _fd_headers() -> dict:
    return {"X-Auth-Token": os.environ.get("FOOTBALL_DATA_API_KEY", "")}


def find_analysis_match(already_analyzed_ids: set[int]) -> dict | None:
    """
    直近5日間の完了試合から分析対象を返す。
    マンU戦は大会（カップ戦・CL等含む）を問わず最優先。マンU戦がなければPL全体から選ぶ。
    already_analyzed_ids: 既に分析済みの試合IDセット
    """
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    if not api_key or api_key == "your_football_data_api_key_here":
        return None

    today = datetime.now(JST).date()
    date_from = (today - timedelta(days=5)).isoformat()
    date_to = today.isoformat()

    # マンU戦を全大会横断で優先探索（PLに限らずカップ戦・CL等も対象）
    try:
        r = requests.get(
            f"{FOOTBALL_DATA_BASE}/teams/{MAN_UNITED_ID}/matches",
            headers=_fd_headers(),
            params={"status": "FINISHED", "dateFrom": date_from, "dateTo": date_to},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        r.raise_for_status()
        man_utd_matches = [m for m in r.json().get("matches", []) if m.get("id") not in already_analyzed_ids]
    except Exception as e:
        print(f"[match_analyzer] マンU試合データ取得失敗: {e}")
        man_utd_matches = []

    if man_utd_matches:
        return sorted(man_utd_matches, key=lambda m: m.get("utcDate", ""), reverse=True)[0]

    # マンU戦がなければPL全体から通常の分析対象を選ぶ
    try:
        r = requests.get(
            f"{FOOTBALL_DATA_BASE}/competitions/{PL_ID}/matches",
            headers=_fd_headers(),
            params={"status": "FINISHED", "dateFrom": date_from, "dateTo": date_to},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        r.raise_for_status()
        matches = r.json().get("matches", [])
    except Exception as e:
        print(f"[match_analyzer] 試合データ取得失敗: {e}")
        return None

    matches = [m for m in matches if m.get("id") not in already_analyzed_ids]
    if not matches:
        return None

    return sorted(matches, key=lambda m: m.get("utcDate", ""), reverse=True)[0]


def find_preview_match(already_previewed_ids: set[int], lookahead_days: int = 2) -> dict | None:
    """
    直近 lookahead_days 日以内に予定されているマンU戦（全大会）から
    プレビュー未生成の直近の1試合を返す。
    """
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    if not api_key or api_key == "your_football_data_api_key_here":
        return None

    today = datetime.now(JST).date()
    date_from = today.isoformat()
    date_to = (today + timedelta(days=lookahead_days)).isoformat()

    try:
        r = requests.get(
            f"{FOOTBALL_DATA_BASE}/teams/{MAN_UNITED_ID}/matches",
            headers=_fd_headers(),
            params={"status": "SCHEDULED", "dateFrom": date_from, "dateTo": date_to},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        r.raise_for_status()
        matches = r.json().get("matches", [])
    except Exception as e:
        print(f"[match_analyzer] マンU試合予定取得失敗: {e}")
        return None

    matches = [m for m in matches if m.get("id") not in already_previewed_ids]
    if not matches:
        return None

    # 最も近い試合を優先
    return sorted(matches, key=lambda m: m.get("utcDate", ""))[0]


def _build_match_facts(match: dict) -> str:
    """試合の事実データ（得点・カード等）をテキスト化"""
    home = match.get("homeTeam", {}).get("name", "?")
    away = match.get("awayTeam", {}).get("name", "?")
    ft = match.get("score", {}).get("fullTime", {})
    ht = match.get("score", {}).get("halfTime", {})

    lines = [
        f"【試合データ】",
        f"カード: {home} vs {away}",
        f"最終スコア: {ft.get('home', 0)} - {ft.get('away', 0)}",
        f"前半スコア: {ht.get('home', '?')} - {ht.get('away', '?')}",
        f"第{match.get('matchday', '?')}節",
    ]

    goals = match.get("goals", [])
    if goals:
        lines.append("得点:")
        for g in goals:
            scorer = g.get("scorer", {}).get("name", "?")
            minute = g.get("minute", "?")
            assist = (g.get("assist") or {}).get("name", "")
            team = g.get("team", {}).get("shortName", "?")
            assist_str = f"（アシスト: {assist}）" if assist else ""
            lines.append(f"  {minute}' {scorer}（{team}）{assist_str}")

    bookings = match.get("bookings", [])
    if bookings:
        lines.append("カード:")
        for b in bookings:
            player = b.get("player", {}).get("name", "?")
            card = "レッド" if "RED" in b.get("card", "") else "イエロー"
            minute = b.get("minute", "?")
            team = b.get("team", {}).get("shortName", "?")
            lines.append(f"  {minute}' {player}（{team}）{card}カード")

    return "\n".join(lines)


def _build_preview_facts(match: dict) -> str:
    """試合予定の事実データ（日時・会場・大会等）をテキスト化"""
    home = match.get("homeTeam", {}).get("name", "?")
    away = match.get("awayTeam", {}).get("name", "?")
    competition = match.get("competition", {}).get("name", "?")
    matchday = match.get("matchday")
    venue = match.get("venue", "")

    kickoff_jst = ""
    utc_date = match.get("utcDate", "")
    if utc_date:
        try:
            dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00")).astimezone(JST)
            kickoff_jst = dt.strftime("%Y年%m月%d日 %H:%M（日本時間）")
        except Exception:
            pass

    lines = [
        "【試合予定データ】",
        f"カード: {home} vs {away}",
        f"大会: {competition}",
    ]
    if matchday:
        lines.append(f"節/ラウンド: 第{matchday}節")
    if kickoff_jst:
        lines.append(f"キックオフ: {kickoff_jst}")
    if venue:
        lines.append(f"会場: {venue}")

    return "\n".join(lines)


ANALYSIS_SYSTEM_PROMPT = """あなたはプレミアリーグ専門の日本語スポーツライターで、戦術分析を得意とします。
""" + PLAYER_NAMES_GUIDE + """
【絶対に守るルール】
- 提供されたソースの情報のみを使用する（知識の補完は禁止）
- 記事中でのソース引用は1ソースあたり最大100語（英語換算）まで
- 著作権を尊重し、原文の長文コピーは行わない

【分析記事の書き方】
- 試合の流れを前半・後半に分けて具体的に解説する
- 両チームの戦術的アプローチ（フォーメーション・プレッシング・ビルドアップ）を分析する
- ターニングポイントとなった場面を特定する
- 主要選手のパフォーマンスを具体的に評価する
- この結果がリーグ順位・残り試合に与える影響を考察する

【出力フォーマット（厳守）】
```
# {クラブ名} {スコア} {クラブ名} 戦術分析｜{節数と簡潔な見どころ}

## 試合の流れ
### 前半
{前半の展開を具体的に}
### 後半
{後半の展開・決定的な場面を具体的に}

## 戦術分析
### {ホームチーム}の狙いと実行
{フォーメーション・プレッシング・攻守の仕組みを分析}
### {アウェイチーム}の狙いと実行
{同上}

## ターニングポイント
{試合を決定づけた場面を1〜2つ具体的に}

## 選手評価
{主要選手3〜5名のパフォーマンスを短く評価}

## この結果が意味するもの
{リーグ順位・残留争い・優勝争いへの影響を2〜3行で}

---
**情報源**
- [{記事タイトル}]({URL}) - {サイト名}
```

記事末尾に以下を追記すること：
<!-- SEO
seo_desc: 試合の要点と分析の価値が伝わる120文字以内の説明文
-->"""


def generate_analysis_article(
    match: dict,
    config_path: str = "config.yaml",
) -> GeneratedArticle | None:
    """試合分析記事を生成する"""
    home = match.get("homeTeam", {}).get("shortName", "?")
    away = match.get("awayTeam", {}).get("shortName", "?")
    ft = match.get("score", {}).get("fullTime", {})
    score_str = f"{ft.get('home', 0)}-{ft.get('away', 0)}"
    matchday = match.get("matchday", "?")

    # 検索クエリを作成
    utc_date = match.get("utcDate", "")
    date_str = ""
    if utc_date:
        try:
            dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
            date_str = dt.strftime("%B %Y")
        except Exception:
            pass

    home_full = match.get("homeTeam", {}).get("name", home)
    away_full = match.get("awayTeam", {}).get("name", away)
    search_query = f"{home_full} {away_full} {score_str} match report analysis Premier League {date_str}"

    print(f"[match_analyzer] 分析対象: {home} {score_str} {away} (第{matchday}節)")
    print(f"[match_analyzer] 検索クエリ: {search_query}")

    # Tavily で試合記事を検索
    search_results = search_articles(search_query, config_path)
    if len(search_results) < 2:
        print(f"[match_analyzer] 記事不足 ({len(search_results)}件)、スキップ")
        return None

    articles = fetch_articles([r.url for r in search_results])
    if not articles:
        print("[match_analyzer] フル記事取得ゼロ、スキップ")
        return None

    # ソースコンテキスト構築
    from synthesizer import build_source_context
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    max_context_words = cfg["search"].get("max_context_words", 500)
    max_quote_words = cfg["search"].get("max_quote_words", 100)

    match_facts = _build_match_facts(match)
    source_ctx = build_source_context(articles, search_results, max_context_words, max_quote_words)

    user_message = (
        f"以下の試合について戦術分析記事を生成してください。\n\n"
        f"{match_facts}\n\n"
        f"--- ソース情報 ---\n{source_ctx}\n--- ここまで ---\n\n"
        f"試合データとソースを踏まえ、指定フォーマットで日本語の分析記事を生成してください。\n"
        f"タイトルは「{home} {score_str} {away} 戦術分析」の形式を含めること。"
    )

    _ssl_verify = os.environ.get("SSL_VERIFY", "true").lower() != "false"
    http_client = httpx.Client(verify=_ssl_verify) if not _ssl_verify else None
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        http_client=http_client,
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=[{"type": "text", "text": ANALYSIS_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message}],
    )

    content = response.content[0].text
    content, meta_description = _extract_seo_meta(content)
    title = _extract_title(content)

    print(f"[match_analyzer] 分析記事生成完了: {title}")
    print(f"[match_analyzer] 使用トークン: input={response.usage.input_tokens}, output={response.usage.output_tokens}")

    return GeneratedArticle(
        title=title,
        content=content,
        sources=search_results,
        meta_description=meta_description,
    )


PREVIEW_SYSTEM_PROMPT = """あなたはプレミアリーグ専門の日本語スポーツライターで、試合プレビューを得意とします。
""" + PLAYER_NAMES_GUIDE + """
【絶対に守るルール】
- 提供されたソースの情報のみを使用する（知識の補完は禁止）
- 記事中でのソース引用は1ソースあたり最大100語（英語換算）まで
- 著作権を尊重し、原文の長文コピーは行わない
- 未確定の予想スタメン・移籍情報・怪我人状況は「〜と報じられている」「〜の可能性がある」等、断定を避けた表現にする
- ソースに情報がない項目（怪我人情報・過去対戦成績など）は無理に埋めず、その旨を簡潔に触れるか省略する

【プレビュー記事の書き方】
- 両チームの直近のコンディション・フォームを具体的に説明する
- 怪我人・出場停止情報があれば明記する（ソースにある場合のみ）
- 過去の対戦成績・因縁があれば触れる
- 予想される戦術的な見どころ・キーとなる対決を分析する
- 勝敗予想は断定せず、注目ポイントとして提示する

【出力フォーマット（厳守）】
```
# {クラブ名} vs {クラブ名} プレビュー｜{大会名}・見どころ

## 試合情報
{日時・会場・大会を簡潔に}

## 両チームのコンディション
### {ホームチーム}
{直近のフォーム・調子}
### {アウェイチーム}
{直近のフォーム・調子}

## 注目の怪我人・出場停止
{ソースにある情報のみ。なければ「特筆すべき情報なし」等}

## 過去の対戦成績
{直近の対戦結果・因縁があれば}

## 試合の見どころ
{戦術的なポイント・キーマッチアップ}

---
**情報源**
- [{記事タイトル}]({URL}) - {サイト名}
```

記事末尾に以下を追記すること：
<!-- SEO
seo_desc: 試合の見どころが伝わる120文字以内の説明文
-->"""


def generate_preview_article(
    match: dict,
    config_path: str = "config.yaml",
) -> GeneratedArticle | None:
    """試合プレビュー記事を生成する"""
    home = match.get("homeTeam", {}).get("shortName", "?")
    away = match.get("awayTeam", {}).get("shortName", "?")
    competition = match.get("competition", {}).get("name", "")
    matchday = match.get("matchday", "?")

    utc_date = match.get("utcDate", "")
    date_str = ""
    if utc_date:
        try:
            dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
            date_str = dt.strftime("%B %Y")
        except Exception:
            pass

    home_full = match.get("homeTeam", {}).get("name", home)
    away_full = match.get("awayTeam", {}).get("name", away)
    search_query = f"{home_full} {away_full} preview team news lineup {competition} {date_str}"

    print(f"[match_analyzer] プレビュー対象: {home} vs {away} ({competition} 第{matchday}節)")
    print(f"[match_analyzer] 検索クエリ: {search_query}")

    search_results = search_articles(search_query, config_path)
    if len(search_results) < 2:
        print(f"[match_analyzer] 記事不足 ({len(search_results)}件)、スキップ")
        return None

    articles = fetch_articles([r.url for r in search_results])
    if not articles:
        print("[match_analyzer] フル記事取得ゼロ、スキップ")
        return None

    from synthesizer import build_source_context
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    max_context_words = cfg["search"].get("max_context_words", 500)
    max_quote_words = cfg["search"].get("max_quote_words", 100)

    preview_facts = _build_preview_facts(match)
    source_ctx = build_source_context(articles, search_results, max_context_words, max_quote_words)

    user_message = (
        f"以下の試合についてプレビュー記事を生成してください。\n\n"
        f"{preview_facts}\n\n"
        f"--- ソース情報 ---\n{source_ctx}\n--- ここまで ---\n\n"
        f"試合データとソースを踏まえ、指定フォーマットで日本語のプレビュー記事を生成してください。\n"
        f"タイトルは「{home} vs {away} プレビュー」の形式を含めること。"
    )

    _ssl_verify = os.environ.get("SSL_VERIFY", "true").lower() != "false"
    http_client = httpx.Client(verify=_ssl_verify) if not _ssl_verify else None
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        http_client=http_client,
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=[{"type": "text", "text": PREVIEW_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message}],
    )

    content = response.content[0].text
    content, meta_description = _extract_seo_meta(content)
    title = _extract_title(content)

    print(f"[match_analyzer] プレビュー記事生成完了: {title}")
    print(f"[match_analyzer] 使用トークン: input={response.usage.input_tokens}, output={response.usage.output_tokens}")

    return GeneratedArticle(
        title=title,
        content=content,
        sources=search_results,
        meta_description=meta_description,
    )
