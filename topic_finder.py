"""
テーマ抽出モジュール
rss:      BBC/Sky Sports/Guardian/MEN/Metro などの RSS フィードから最新記事を取得（推奨）
          + Nitter 経由で Fabrizio Romano ら Twitter アカウントの投稿も取得
trending: Tavily で今日の具体的なPLニュース記事を検索してトピック化
reddit:   r/soccer のホットトピックから抽出
fixed:    設定ファイルの固定テーマリストを使用

Man United 関連トピックは config.yaml の man_united_boost 倍のスコアを付与する。
"""

import os
import re
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import praw
import requests
import yaml
from dotenv import load_dotenv

load_dotenv()

_SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() != "false"

# セクション・一覧ページを除外するパターン
_SECTION_PATTERNS = [
    re.compile(r'/(premier-league|premierleague|football|soccer|transfers|standings|scoreboard|results|fixtures|table|league|news|gossip)/?$', re.I),
    re.compile(r'/_/name/|/league/_/|/scoreboard/_/|/standings/_/', re.I),
    re.compile(r'/sport/football/?$', re.I),
    re.compile(r'/(scores-fixtures|scores_fixtures|transfer-news|transfer-centre|transfer-gossip|gossip-column)/?$', re.I),
    re.compile(r'/premier-league/(scores|fixtures|table|results|standings|scoreboard)/?$', re.I),
]

_SECTION_TITLE_KEYWORDS = [
    "scores & fixtures", "scores & results", "live scores", "match centre",
]

# PLサッカー関連キーワード
_PL_KEYWORDS = [
    "premier league", "arsenal", "chelsea", "liverpool",
    "manchester city", "manchester united", "man city", "man utd",
    "tottenham", "spurs", "newcastle", "aston villa", "brighton",
    "west ham", "everton", "fulham", "brentford", "crystal palace",
    "wolves", "wolverhampton", "ipswich", "nottingham forest",
    "bournemouth", "southampton", "leicester", "leeds", "luton",
    "transfer", "epl", "prem", "matchday", "matchweek",
    "europa league", "conference league", "champions league",
    "here we go",  # Fabrizio Romano の決め台詞
]

# Man United 関連キーワード
_MAN_UNITED_KEYWORDS = [
    "manchester united", "man united", "man utd", "mufc", "old trafford",
    "red devils", "ruben amorim", "amorim",
]

# 欧州リーグ・クラブキーワード（プレミアリーグを除く）
_EUROPE_CLUB_KEYWORDS = [
    # スペイン
    "real madrid", "barcelona", "atletico madrid", "atletico de madrid",
    "sevilla", "valencia", "real sociedad", "athletic bilbao", "la liga", "laliga",
    # ドイツ
    "bayern", "bayer leverkusen", "borussia dortmund", "rb leipzig",
    "eintracht", "wolfsburg", "bundesliga",
    # イタリア
    "juventus", "ac milan", "inter milan", "napoli", "as roma", "lazio",
    "atalanta", "fiorentina", "serie a",
    # フランス
    "psg", "paris saint-germain", "marseille", "lyon", "monaco", "ligue 1",
    # オランダ・ポルトガル他
    "ajax", "psv", "feyenoord", "porto", "benfica", "sporting cp",
    # 欧州大会（クラブ名なしでも欧州記事と判断）
    "champions league", "europa league", "conference league",
    "ucl", "uel", "uecl",
]

# グローバル移籍キーワード（リーグ不問）
_TRANSFER_GLOBAL_KEYWORDS = [
    "transfer", "loan", "sign", "signing", "deal", "bid", "fee", "contract",
    "negotiate", "negotiation", "buy", "sell", "linked", "offer", "agreed",
    "here we go", "permanent", "option to buy", "medical", "release clause",
    "free agent", "depart", "departure", "arrival", "confirmed", "swap deal",
]

# 非フットボールURLパターン
_NON_FOOTBALL_PATH = re.compile(
    r"/(racing|nba|boxing|golf|cricket|rugby|tennis|cycling|darts|f1|moto|swimming|athletics)/",
    re.I,
)

# 「プレミアリーグ」という語が入っても非サッカーのコンテンツ
_FALSE_POSITIVE_KEYWORDS = [
    "premier league night", "premier league darts",
    "wsl", "women's super league", "fa women", "barclays women", "women's premier league",
]

# Nitter インスタンス（複数試行、安定順）
_NITTER_INSTANCES = [
    "https://xcancel.com",           # US, 高安定
    "https://nitter.net",            # Netherlands, 公式
    "https://nitter.kareem.one",     # Singapore, アジア圏向け
    "https://nitter.privacyredirect.com",  # Finland
    "https://nuku.trabun.org",       # Chile
    "https://nitter.poast.org",      # US
    "https://lightbrd.com",          # Turkey
]


def _is_article_url(url: str) -> bool:
    path = urlparse(url).path
    for pat in _SECTION_PATTERNS:
        if pat.search(path):
            return False
    parts = [p for p in path.strip("/").split("/") if p]
    return len(parts) >= 2


def _is_article_title(title: str) -> bool:
    tl = title.lower()
    return not any(kw in tl for kw in _SECTION_TITLE_KEYWORDS)


def _is_pl_football_content(title: str, url: str) -> bool:
    if _NON_FOOTBALL_PATH.search(url):
        return False
    tl = title.lower()
    if any(kw in tl for kw in _FALSE_POSITIVE_KEYWORDS):
        return False
    return any(kw in tl for kw in _PL_KEYWORDS)


def _is_man_united_related(title: str) -> bool:
    tl = title.lower()
    return any(kw in tl for kw in _MAN_UNITED_KEYWORDS)


def _is_europe_football(title: str, url: str = "") -> bool:
    if _NON_FOOTBALL_PATH.search(url):
        return False
    tl = title.lower()
    if any(kw in tl for kw in _FALSE_POSITIVE_KEYWORDS):
        return False
    return any(kw in tl for kw in _EUROPE_CLUB_KEYWORDS)


def _is_global_transfer(title: str, url: str = "") -> bool:
    if _NON_FOOTBALL_PATH.search(url):
        return False
    tl = title.lower()
    if any(kw in tl for kw in _FALSE_POSITIVE_KEYWORDS):
        return False
    return any(kw in tl for kw in _TRANSFER_GLOBAL_KEYWORDS)


def _pub_score(entry: dict) -> int:
    """RSS エントリの公開時刻から新着スコアを算出（新しいほど高い）"""
    published = entry.get("published_parsed")
    if not published:
        return 0
    try:
        pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 60
        return max(0, int(100_000 - age_min))
    except Exception:
        return 0


@dataclass
class Topic:
    title: str
    url: str | None = None
    score: int = 0


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_rss_topics(
    feed_urls: list[str],
    limit: int = 20,
    man_united_boost: float = 1.0,
    content_filter=None,
) -> list[Topic]:
    """
    RSS フィードから最新記事を取得してトピック候補を返す。
    Man United 関連記事は man_united_boost 倍のスコアを付与する。
    content_filter: (title, url) -> bool。None の場合は _is_pl_football_content を使用。
    """
    if content_filter is None:
        content_filter = _is_pl_football_content

    topics: list[Topic] = []
    seen: set[str] = set()

    session = requests.Session()
    session.verify = _SSL_VERIFY
    session.headers["User-Agent"] = "premier-blog-rss/1.0"

    for feed_url in feed_urls:
        try:
            resp = session.get(feed_url, timeout=15)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"[topic_finder] RSS 取得失敗 {feed_url}: {e}")
            continue

        for entry in feed.entries:
            title = entry.get("title", "").strip()
            url = entry.get("link", "").strip()

            if not title or not url:
                continue
            if not _is_article_url(url):
                continue
            if not _is_article_title(title):
                continue
            if not content_filter(title, url):
                continue

            normalized = re.sub(r"\s+", " ", title.lower())[:40]
            if normalized in seen:
                continue
            seen.add(normalized)

            score = _pub_score(entry)

            # Man United 関連はスコアをブースト
            if man_united_boost > 1.0 and _is_man_united_related(title):
                score = int(score * man_united_boost)

            topics.append(Topic(title=title, url=url, score=score))

    topics.sort(key=lambda t: t.score, reverse=True)
    print(f"[topic_finder] RSS: {len(topics)} 件の最新記事を取得")
    return topics[:limit]


def get_nitter_topics(
    accounts: list[str],
    limit: int = 10,
    man_united_boost: float = 1.0,
    content_filter=None,
) -> list[Topic]:
    """
    Nitter 経由で Twitter アカウントの投稿をトピックとして取得する。
    Nitter インスタンスが不安定なため複数を順番に試行し、失敗しても続行する。
    取得したツイートは Jina Reader で取得できないため URL は保持するが、
    researcher ではツイートタイトルを検索クエリとして使用する。
    content_filter: (title, url) -> bool。None の場合は _is_pl_football_content を使用。
    """
    if content_filter is None:
        content_filter = lambda t, u="": _is_pl_football_content(t, u)

    topics: list[Topic] = []
    seen: set[str] = set()

    session = requests.Session()
    session.verify = _SSL_VERIFY
    session.headers["User-Agent"] = "premier-blog-rss/1.0"

    for account in accounts:
        fetched = False
        for instance in _NITTER_INSTANCES:
            rss_url = f"{instance}/{account}/rss"
            try:
                resp = session.get(rss_url, timeout=10)
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
                if not feed.entries:
                    continue

                account_topics = []
                for entry in feed.entries[:20]:
                    # ツイート本文は summary フィールドにある
                    title = entry.get("title", "").strip()
                    summary = re.sub(r"<[^>]+>", "", entry.get("summary", "")).strip()
                    # タイトルが短い場合は summary を使う
                    text = summary if len(summary) > len(title) else title

                    if not text or len(text) < 20:
                        continue
                    # xcancel 等のホワイトリスト拒否メッセージは除外
                    if "not yet whitelist" in text or "rss [at]" in text.lower():
                        continue
                    # topic_title として使われる先頭部分でフィルタリング（全文一致だと
                    # 後半の別ツイート内容で通過し、先頭の無関係テキストが検索クエリになる）
                    if not content_filter(text[:200]):
                        continue

                    normalized = re.sub(r"\s+", " ", text.lower())[:40]
                    if normalized in seen:
                        continue
                    seen.add(normalized)

                    score = _pub_score(entry)
                    if man_united_boost > 1.0 and _is_man_united_related(text):
                        score = int(score * man_united_boost)

                    # ツイートは記事タイトルとして扱い、URLは参照のみ（Jinaでは取得しない）
                    tweet_url = entry.get("link", "").replace(instance, "https://x.com")
                    topic_title = f"[{account}] {text[:120]}"
                    account_topics.append(Topic(title=topic_title, url=tweet_url, score=score))

                if account_topics:
                    topics.extend(account_topics)
                    print(f"[topic_finder] Nitter @{account}: {len(account_topics)} 件取得 ({instance})")
                    fetched = True
                    break

            except Exception as e:
                print(f"[topic_finder] Nitter {instance} 失敗: {e}")
                continue

        if not fetched:
            print(f"[topic_finder] @{account} の全 Nitter インスタンスで取得失敗（スキップ）")

    return sorted(topics, key=lambda t: t.score, reverse=True)[:limit]


def get_trending_topics(limit: int = 10, config_path: str = "config.yaml") -> list[Topic]:
    from tavily import TavilyClient

    config = load_config(config_path)
    whitelist = config["sources"]["whitelist"]

    include_domains = []
    for entry in whitelist:
        domain = entry.split("/")[0]
        if domain not in include_domains:
            include_domains.append(domain)

    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    if not _SSL_VERIFY:
        client.session.verify = False  # type: ignore[attr-defined]

    search_queries = [
        "Premier League news today",
        "Manchester United transfer news today",
        "Premier League transfer news rumours signing",
    ]

    seen: set[str] = set()
    all_items: list[dict] = []

    for query in search_queries:
        try:
            resp = client.search(query, search_depth="advanced", max_results=12, include_domains=include_domains)
            all_items.extend(resp.get("results", []))
        except Exception as e:
            print(f"[topic_finder] クエリ失敗 '{query}': {e}")

    man_united_boost = config.get("topic", {}).get("man_united_boost", 1.0)
    topics = []
    for item in all_items:
        url = item.get("url", "")
        title = item.get("title", "").strip()
        if not title or not url or not _is_article_url(url) or not _is_article_title(title):
            continue
        normalized = re.sub(r"\s+", " ", title.lower())[:40]
        if normalized in seen:
            continue
        seen.add(normalized)

        score = int(item.get("score", 0.0) * 10000)
        if man_united_boost > 1.0 and _is_man_united_related(title):
            score = int(score * man_united_boost)

        topics.append(Topic(title=title, url=url, score=score))

    topics.sort(key=lambda t: t.score, reverse=True)
    print(f"[topic_finder] Trending: {len(topics)} 件の具体的なニュースを取得")
    return topics[:limit]


def get_reddit_topics(limit: int = 5) -> list[Topic]:
    reddit = praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=os.environ.get("REDDIT_USER_AGENT", "premier-blog/1.0"),
    )
    pl_keywords = [
        "premier league", "pl ", "epl", "arsenal", "chelsea", "liverpool",
        "manchester", "tottenham", "newcastle", "aston villa", "brighton", "west ham",
    ]
    topics = []
    subreddit = reddit.subreddit("soccer")
    for submission in subreddit.hot(limit=limit * 3):
        if any(kw in submission.title.lower() for kw in pl_keywords):
            topics.append(Topic(title=submission.title, url=submission.url, score=submission.score))
            if len(topics) >= limit:
                break
    if len(topics) < limit:
        for submission in subreddit.hot(limit=limit * 2):
            if submission.url not in [t.url for t in topics]:
                topics.append(Topic(title=submission.title, url=submission.url, score=submission.score))
                if len(topics) >= limit:
                    break
    return topics[:limit]


def get_fixed_topics(themes: list[str]) -> list[Topic]:
    return [Topic(title=theme) for theme in themes]


def find_topics_transfers(config_path: str = "config.yaml") -> list[Topic]:
    """移籍ニュース専用トピック取得（Twitter トレンド優先、Man United 以外）"""
    config = load_config(config_path)
    cfg = config.get("topic_transfers", {})

    nitter_accounts = cfg.get("nitter_accounts", ["FabrizioRomano"])
    rss_feeds = cfg.get("rss_feeds", [])
    limit = cfg.get("limit", 10)
    exclude_man_united = cfg.get("exclude_man_united", True)

    def transfer_filter(title: str, url: str = "") -> bool:
        return _is_global_transfer(title, url)

    topics: list[Topic] = []

    if nitter_accounts:
        nitter_topics = get_nitter_topics(
            accounts=nitter_accounts,
            limit=limit,
            content_filter=transfer_filter,
        )
        topics.extend(nitter_topics)

    if rss_feeds:
        rss_topics = get_rss_topics(
            feed_urls=rss_feeds,
            limit=limit,
            content_filter=transfer_filter,
        )
        topics.extend(rss_topics)

    if exclude_man_united:
        topics = [t for t in topics if not _is_man_united_related(t.title)]

    topics.sort(key=lambda t: t.score, reverse=True)
    seen: set[str] = set()
    deduped = []
    for t in topics:
        key = re.sub(r"\s+", " ", t.title.lower())[:40]
        if key not in seen:
            seen.add(key)
            deduped.append(t)

    print(f"[topic_finder] 移籍トピック: {len(deduped)} 件取得（MU除外: {exclude_man_united}）")
    return deduped[:limit]


def find_topics_europe(config_path: str = "config.yaml") -> list[Topic]:
    """欧州フットボール専用トピック取得（Twitter トレンド優先、PL 以外の欧州クラブ情報）"""
    config = load_config(config_path)
    cfg = config.get("topic_europe", {})

    nitter_accounts = cfg.get("nitter_accounts", ["FabrizioRomano"])
    rss_feeds = cfg.get("rss_feeds", [])
    limit = cfg.get("limit", 10)

    def europe_filter(title: str, url: str = "") -> bool:
        return _is_europe_football(title, url)

    topics: list[Topic] = []

    if nitter_accounts:
        nitter_topics = get_nitter_topics(
            accounts=nitter_accounts,
            limit=limit,
            content_filter=europe_filter,
        )
        topics.extend(nitter_topics)

    if rss_feeds:
        rss_topics = get_rss_topics(
            feed_urls=rss_feeds,
            limit=limit,
            content_filter=europe_filter,
        )
        topics.extend(rss_topics)

    topics.sort(key=lambda t: t.score, reverse=True)
    seen: set[str] = set()
    deduped = []
    for t in topics:
        key = re.sub(r"\s+", " ", t.title.lower())[:40]
        if key not in seen:
            seen.add(key)
            deduped.append(t)

    print(f"[topic_finder] 欧州トピック: {len(deduped)} 件取得")
    return deduped[:limit]


def find_topics(config_path: str = "config.yaml") -> list[Topic]:
    config = load_config(config_path)
    topic_cfg = config.get("topic", {})
    mode = topic_cfg.get("mode", "rss")
    man_united_boost = float(topic_cfg.get("man_united_boost", 1.0))

    if mode == "rss":
        try:
            feed_urls = topic_cfg.get("rss_feeds", [])
            nitter_accounts = topic_cfg.get("nitter_accounts", [])
            limit = topic_cfg.get("rss_limit", 20)
            if not feed_urls:
                raise ValueError("rss_feeds が config.yaml に未設定")

            rss_topics = get_rss_topics(feed_urls=feed_urls, limit=limit, man_united_boost=man_united_boost)

            # Nitter トピックを合流させてスコア順に統合
            nitter_topics = []
            if nitter_accounts:
                nitter_topics = get_nitter_topics(
                    accounts=nitter_accounts,
                    limit=limit,
                    man_united_boost=man_united_boost,
                )

            combined = rss_topics + nitter_topics
            combined.sort(key=lambda t: t.score, reverse=True)
            combined = combined[:limit]

            if combined:
                mu_count = sum(1 for t in combined if _is_man_united_related(t.title))
                print(f"[topic_finder] 統合: {len(combined)} 件（うち Man United 関連 {mu_count} 件）")
                return combined

            print("[topic_finder] RSS 取得失敗、trending にフォールバック")
        except Exception as e:
            print(f"[topic_finder] RSS 取得失敗 ({e})、trending にフォールバック")

    if mode in ("rss", "trending"):
        try:
            limit = topic_cfg.get("trending_limit", 10)
            topics = get_trending_topics(limit=limit, config_path=config_path)
            if topics:
                return topics
            print("[topic_finder] Trending 取得失敗、固定テーマにフォールバック")
        except Exception as e:
            print(f"[topic_finder] Trending 取得失敗 ({e})、固定テーマにフォールバック")

    if mode == "reddit":
        try:
            topics = get_reddit_topics(limit=topic_cfg.get("reddit_limit", 5))
            print(f"[topic_finder] Reddit から {len(topics)} 件のトピックを取得")
            return topics
        except Exception as e:
            print(f"[topic_finder] Reddit 取得失敗 ({e})、固定テーマにフォールバック")

    themes = topic_cfg.get("fixed_themes", ["Manchester United latest news"])
    topics = get_fixed_topics(themes)
    print(f"[topic_finder] 固定テーマから {len(topics)} 件のトピックを使用")
    return topics


def select_topic(topics: list[Topic]) -> Topic:
    if any(t.score > 0 for t in topics):
        return max(topics, key=lambda t: t.score)
    return random.choice(topics)


if __name__ == "__main__":
    topics = find_topics()
    for t in topics[:15]:
        title_safe = t.title.encode("cp932", errors="replace").decode("cp932")
        mu = " [MU]" if _is_man_united_related(t.title) else ""
        print(f"  [{t.score:>7}]{mu} {title_safe}")
    selected = select_topic(topics)
    title_safe = selected.title.encode("cp932", errors="replace").decode("cp932")
    print(f"\n選択されたトピック: {title_safe}")
