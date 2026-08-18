"""
コンテンツ取得モジュール
Jina Reader (https://r.jina.ai/{URL}) で各記事の本文を取得
"""

import os
import time
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv

load_dotenv()

JINA_BASE = "https://r.jina.ai/"
# 企業プロキシ環境など SSL 検証が通らない場合は .env で SSL_VERIFY=false を設定
_SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() != "false"
REQUEST_TIMEOUT = 30
RETRY_LIMIT = 2
RETRY_WAIT = 2.0


@dataclass
class FetchedArticle:
    url: str
    title: str
    content: str
    word_count: int = 0

    def __post_init__(self):
        self.word_count = len(self.content.split())


_BLOCK_MARKERS = [
    "you have been blocked",
    "cloudflare ray id",
    "enable javascript and cookies",
    "please enable cookies",
    "checking your browser",
]


def _looks_blocked(text: str) -> bool:
    """Cloudflare等のボット対策ブロックページを検出する"""
    lower = text.lower()
    return any(m in lower for m in _BLOCK_MARKERS)


def _fetch_with_browser(url: str) -> FetchedArticle | None:
    """Jina Readerがボット対策でブロックされた場合のフォールバック。
    ヘッドレスブラウザで直接アクセスしてJSレンダリング後の本文を取得する。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"[fetcher] playwright未インストールのためブラウザ取得をスキップ: {url}")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                ignore_https_errors=True,
            )
            page = context.new_page()
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            title = page.title()
            text = page.inner_text("body")
            browser.close()

        if len(text.strip()) < 100 or _looks_blocked(text):
            print(f"[fetcher] ブラウザ取得もブロック/内容不足: {url}")
            return None

        article = FetchedArticle(url=url, title=title, content=text.strip())
        print(f"[fetcher] ブラウザ経由で取得完了 ({article.word_count} words): {url}")
        return article
    except Exception as e:
        print(f"[fetcher] ブラウザ取得失敗: {url} ({e})")
        return None


def fetch_article(url: str) -> FetchedArticle | None:
    jina_url = JINA_BASE + url
    headers = {
        "Accept": "text/plain",
        "X-Return-Format": "markdown",
    }

    for attempt in range(RETRY_LIMIT + 1):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True, verify=_SSL_VERIFY) as client:
                resp = client.get(jina_url, headers=headers)
                resp.raise_for_status()
                text = resp.text

            # Jina レスポンスから title と content を分離
            lines = text.strip().splitlines()
            title = ""
            content_lines = []
            for i, line in enumerate(lines):
                if line.startswith("Title:"):
                    title = line.removeprefix("Title:").strip()
                elif line.startswith("URL Source:"):
                    continue
                elif line.startswith("Markdown Content:"):
                    content_lines = lines[i + 1:]
                    break

            if not content_lines:
                content_lines = lines

            content = "\n".join(content_lines).strip()

            if len(content) < 100:
                print(f"[fetcher] コンテンツが短すぎます: {url}")
                return None

            if _looks_blocked(content):
                print(f"[fetcher] ボット対策ブロックを検出、ブラウザ取得にフォールバック: {url}")
                return _fetch_with_browser(url)

            article = FetchedArticle(url=url, title=title, content=content)
            print(f"[fetcher] 取得完了 ({article.word_count} words): {url}")
            return article

        except httpx.HTTPStatusError as e:
            print(f"[fetcher] HTTP {e.response.status_code}: {url}")
            return None
        except Exception as e:
            if attempt < RETRY_LIMIT:
                print(f"[fetcher] リトライ {attempt + 1}/{RETRY_LIMIT}: {url} ({e})")
                time.sleep(RETRY_WAIT)
            else:
                print(f"[fetcher] 取得失敗: {url} ({e})")
                return None

    return None


def fetch_articles(urls: list[str], delay: float = 1.5) -> list[FetchedArticle]:
    articles = []
    for i, url in enumerate(urls):
        if i > 0:
            time.sleep(delay)
        article = fetch_article(url)
        if article:
            articles.append(article)
    return articles


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.bbc.com/sport/football/premier-league"
    article = fetch_article(url)
    if article:
        print(f"Title: {article.title}")
        print(f"Words: {article.word_count}")
        print(article.content[:500])
