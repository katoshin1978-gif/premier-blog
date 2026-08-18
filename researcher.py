"""
リサーチモジュール
Tavily Search API でホワイトリストドメインから関連記事を検索。
セクション・一覧ページを除外し、個別記事のみを収集する。

Man United 関連トピックの場合は Manchester Evening News・Metro を含む
専用補完検索を追加で実行し、記事数を確保する。
"""

import os
import re
import unicodedata
import warnings
from dataclasses import dataclass
from urllib.parse import urlparse

import anthropic
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
    re.compile(r'/transfers/wettbewerb/', re.I),  # transfermarkt 全移籍一覧ページを除外
    re.compile(r'/(topics?|author|authors|profile|contributors?|journalist|staff|reporter)/', re.I),  # 記者プロフィール・タグアーカイブページを除外
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


def _is_japanese(text: str) -> bool:
    """テキストに日本語文字（ひらがな・カタカナ・漢字）が含まれるか判定"""
    for ch in text:
        name = unicodedata.name(ch, "")
        if "HIRAGANA" in name or "KATAKANA" in name or "CJK" in name:
            return True
    return False


_EN_STOPWORDS = {
    "the", "a", "an", "of", "to", "for", "and", "in", "on", "with", "is",
    "are", "at", "from", "as", "his", "her", "he", "she", "will", "has",
    "have", "been", "be", "it", "this", "that", "after", "before", "over",
}


def _looks_non_english(text: str) -> bool:
    """英語ストップワードを含まないラテン文字テキストを非英語（伊・西・仏語等）と推定する"""
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if len(words) < 5:
        return False
    return not any(w in _EN_STOPWORDS for w in words)


def _translate_to_english_query(topic: str, context: str = "default") -> str:
    """非英語トピックをTavily検索用の英語クエリに変換する（Claude API使用）"""
    _ssl = os.environ.get("SSL_VERIFY", "true").lower() != "false"
    if context == "longtail":
        instruction = (
            f"Convert this football reference/list topic (it may be in Japanese) to an "
            f"English search query optimized for finding Wikipedia or Transfermarkt reference "
            f"pages rather than news articles (max 12 words, no punctuation). Include specific "
            f"terms like 'squad', 'kader', 'all-time', 'list', 'records', 'history' where "
            f"relevant so the query matches list/reference pages:\n{topic}"
        )
    else:
        instruction = (
            f"Convert this football-related topic (it may be in Japanese, Italian, "
            f"Spanish, or another language) to a concise English search query "
            f"(player/club names in English, max 10 words, no punctuation):\n{topic}"
        )
    try:
        import httpx
        http_client = httpx.Client(verify=False) if not _ssl else None
        client = anthropic.Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            http_client=http_client,
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": instruction,
            }],
        )
        query = resp.content[0].text.strip()
        print(f"[researcher] 非英語トピック翻訳: '{topic[:40]}' → '{query}'")
        return query
    except Exception as e:
        print(f"[researcher] 翻訳失敗（原文使用）: {e}")
        return topic


def _build_query(topic: str, context: str = "default") -> str:
    # Nitter ツイートトピックは先頭の "[アカウント名] " を除去してクエリに使う
    topic = re.sub(r"^\[.*?\]\s*", "", topic).strip()
    # @メンション記号を除去（翻訳失敗時の保険。"@RealBetis" → "RealBetis"）
    topic = re.sub(r"@(\w+)", r"\1", topic).strip()
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
    days: int | None = 14,
) -> list[dict]:
    try:
        params: dict = dict(
            query=query,
            search_depth="basic",
            max_results=max_results,
            include_domains=include_domains,
        )
        if days is not None:
            params["days"] = days  # 直近N日の記事に限定（オフシーズン対応、既定14日）
        resp = client.search(**params)
        return resp.get("results", [])
    except Exception as e:
        print(f"[researcher] 検索失敗 '{query}': {e}")
        return []


def search_articles(query: str, config_path: str = "config.yaml", context: str = "default") -> list[SearchResult]:
    config = load_config(config_path)
    # ロングテール（歴代記録・一覧等）はニュースサイトに情報が無いため
    # Wikipedia・公式サイト等の参照系ソースを使う
    if context == "longtail" and config["sources"].get("longtail_whitelist"):
        whitelist = config["sources"]["longtail_whitelist"]
    else:
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

    # 日本語・非英語（伊語ツイート等）トピックは英語クエリに変換してから検索
    search_base = _translate_to_english_query(query, context=context) if (_is_japanese(query) or _looks_non_english(query)) else query
    search_query = _build_query(search_base, context)
    # ロングテール（歴代記録・背番号一覧等）は直近ニュースではないため期間制限を外す
    days = None if context == "longtail" else 14
    all_items = _run_search(client, search_query, include_domains, max_results, days=days)

    # Man United 関連トピックの場合は MEN・Metro を含む専用クエリを追加実行（defaultコンテキストのみ）
    if context == "default" and (_is_man_united_topic(query) or _is_man_united_topic(search_base)):
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
