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
