"""
X（Twitter）自動投稿モジュール
記事投稿時にツイートを自動送信する
"""

import os
import ssl
import tweepy
from dotenv import load_dotenv

load_dotenv()

# 企業プロキシ環境のSSL証明書エラー対応
if os.environ.get("SSL_VERIFY", "true").lower() == "false":
    ssl._create_default_https_context = ssl._create_unverified_context


def _get_client() -> tweepy.Client | None:
    ck  = os.environ.get("X_CONSUMER_KEY", "")
    cs  = os.environ.get("X_CONSUMER_SECRET", "")
    at  = os.environ.get("X_ACCESS_TOKEN", "")
    ats = os.environ.get("X_ACCESS_TOKEN_SECRET", "")
    if not all([ck, cs, at, ats]):
        return None
    client = tweepy.Client(
        consumer_key=ck,
        consumer_secret=cs,
        access_token=at,
        access_token_secret=ats,
    )
    # 企業プロキシ環境のSSL証明書エラー対応
    if os.environ.get("SSL_VERIFY", "true").lower() == "false":
        client.session.verify = False
    return client


def post_article(title: str, url: str, meta_description: str = "") -> bool:
    """記事をXに投稿する。失敗しても例外を投げない。"""
    client = _get_client()
    if not client:
        print("[x_poster] APIキー未設定のためスキップ")
        return False

    # ツイート本文を組み立て（280文字以内）
    # URL は X 側で23文字換算
    hashtags = "#プレミアリーグ #PremierLeague"
    url_len = 24  # URL + 改行

    # タイトル優先、余裕があれば説明文を追加
    base = f"{title}\n\n{url}\n\n{hashtags}"
    if len(base) > 280:
        # タイトルを切り詰める
        max_title = 280 - url_len - len(hashtags) - 4
        title = title[:max_title] + "…"
        base = f"{title}\n\n{url}\n\n{hashtags}"

    # 説明文を挿入できる余裕があれば追加
    if meta_description:
        with_desc = f"{title}\n\n{meta_description}\n\n{url}\n\n{hashtags}"
        if len(with_desc) <= 280:
            base = with_desc

    try:
        response = client.create_tweet(text=base)
        tweet_id = response.data["id"]
        print(f"[x_poster] ツイート完了: https://x.com/i/web/status/{tweet_id}")
        return True
    except Exception as e:
        print(f"[x_poster] ツイート失敗: {e}")
        return False
