"""
リサーチモジュール
Tavily Search API でホワイトリストドメインから関連記事を検索。
セクション・一覧ページを除外し、個別記事のみを収集する。

Man United 関連トピックの場合は Manchester Evening News・Metro を含む
専用補完検索を追加で実行し、記事数を確保する。
"""

import os
import re
import warnings
from dataclasses import dataclass
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()

_SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() != "false"
if not _SSL_VERIFY:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    os.environ.setdefault("CURL_CA_BUNDLE", "")
    os.environ.setdefault("REQUESTS_CA_BUNDLE", "")

_SECTION_PATTERNS = [
    re.compile(r'/(premier-league|premierleague|football|soccer|transfers|standings|scoreboard|results|fixtures|table|league|news|gossip)/?$', re.I),
    re.compile(r'/_/name/|/league/_/|/scoreboard/_/|/standings/_/', re.I),
    re.compile(r'/sport/football/?$', re.I),
    re.compile(r'/(scores-fixtures|scores_fixtures|transfer-news|transfer-centre|transfer-gossip|gossip-column)/?$', re.I),
    re.compile(r'/premier-league/(scores|fixtures|table|results|standings|scoreboard)/?$', re.I),
]

# Man United 関連と判定するキーワード
_MAN_UNITED_KEYWORDS = [
    "manchester united", "man united", "man utd", "mufc", "old trafford",
    "red devils", "ruben amorim", "amorim",
]

# Man United 専用の補完検索に使うドメイン（メインホワイトリストに加えて優先）
_MAN_UNITED_EXTRA_DOMAINS = [
    "manchestereveningnews.co.uk",
    "metro.co.uk",
]


def _is_article_url(url: str) -> bool:
    path = urlparse(url).path
    for pat in _SECTION_PATTERNS:
        if pat.search(path):
            return False
    parts = [p for p in path.strip("/").split("/") if p]
    return len(parts) >= 2


def _is_man_united_topic(query: str) -> bool:
    ql = query.lower()
    return any(kw in ql for kw in _MAN_UNITED_KEYWORDS)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    domain: str = ""
    score: float = 0.0

    def __post_init__(self):
        if not self.domain:
            self.domain = urlparse(self.url).netloc.lstrip("www.")


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_whitelisted(url: str, whitelist: list[str]) -> bool:
    parsed = urlparse(url)
    domain_path = (parsed.netloc + parsed.path).lstrip("www.")
    return any(domain_path.startswith(w.lstrip("www.")) for w in whitelist)


def _build_query(topic: str, context: str = "default") -> str:
    # Nitter ツイートトピックは先頭の "[アカウント名] " を除去してクエリに使う
    topic = re.sub(r"^\[.*?\]\s*", "", topic).strip()
    if len(topic.split()) >= 5:
        return topic
    if context in ("europe", "transfers"):
        return f"{topic} football"
    return f"{topic} Premier League"


def _run_search(
    client: TavilyClient,
    query: str,
    include_domains: list[str],
    max_results: int,
) -> list[dict]:
    try:
        resp = client.search(
            query=query,
            search_depth="basic",
            max_results=max_results,
            include_domains=include_domains,
            days=7,    # 直近1週間の記事のみ
        )
        return resp.get("results", [])
    except Exception as e:
        print(f"[researcher] 検索失敗 '{query}': {e}")
        return []


def search_articles(query: str, config_path: str = "config.yaml", context: str = "default") -> list[SearchResult]:
    config = load_config(config_path)
    whitelist = config["sources"]["whitelist"]
    max_results = config["search"]["max_results"]

    include_domains = []
    for entry in whitelist:
        domain = entry.split("/")[0]
        if domain not in include_domains:
            include_domains.append(domain)

    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    if not _SSL_VERIFY:
        client.session.verify = False  # type: ignore[attr-defined]

    search_query = _build_query(query, context)
    all_items = _run_search(client, search_query, include_domains, max_results)

    # Man United 関連トピックの場合は MEN・Metro を含む専用クエリを追加実行（defaultコンテキストのみ）
    if context == "default" and _is_man_united_topic(query):
        mu_domains = list(set(include_domains + _MAN_UNITED_EXTRA_DOMAINS))
        mu_query = f"Manchester United {search_query}" if "manchester united" not in search_query.lower() else search_query
        extra_items = _run_search(client, mu_query, mu_domains, max_results)
        all_items = all_items + extra_items
        print(f"[researcher] Man United 補完検索を実行: '{mu_query}'")

    # 重複除去＋フィルタリング
    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    seen_domains: dict[str, int] = {}

    for item in all_items:
        url = item.get("url", "")
        if url in seen_urls:
            continue
        if not is_whitelisted(url, whitelist):
            continue
        if not _is_article_url(url):
            print(f"[researcher] セクションページをスキップ: {url}")
            continue

        domain = urlparse(url).netloc.lstrip("www.")
        if seen_domains.get(domain, 0) >= 2:
            continue
        seen_domains[domain] = seen_domains.get(domain, 0) + 1
        seen_urls.add(url)

        results.append(SearchResult(
            title=item.get("title", ""),
            url=url,
            snippet=item.get("content", ""),
            score=item.get("score", 0.0),
        ))

    print(f"[researcher] '{search_query}' → {len(results)} 件の個別記事を取得")
    return results


if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Manchester United latest news"
    results = search_articles(query)
    for r in results:
        print(f"  [{r.score:.3f}] [{r.domain}] {r.title}")
        print(f"    {r.url}")
