"""
オーケストレーション
全モジュールを統合して自動投稿パイプラインを実行
"""

import argparse
import hashlib
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Windows コンソールが cp932 の場合でも EMダッシュなど Unicode 文字を出力できるようにする
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yaml
from dotenv import load_dotenv

from fetcher import fetch_articles
from image_fetcher import fetch_image, fetch_player_images
from publisher import (
    fetch_post_for_update,
    html_to_markdown_for_update,
    insert_player_images,
    publish_draft,
    upload_media,
)
from researcher import SearchResult, _is_article_url, is_whitelisted, search_articles
from synthesizer import generate_article, generate_update_article
from topic_finder import (
    Topic,
    extract_player_name,
    is_commentary_title,
    find_topics,
    find_topics_transfers,
    find_topics_europe,
    select_topic,
)

load_dotenv()

_SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() != "false"
DB_PATH = "processed.db"
CONFIG_PATH = "config.yaml"
MIN_ARTICLES = 2

# ===== カテゴリ自動判定 =====
_TRANSFER_KW = {
    "transfer", "loan", "sign", "signing", "deal", "bid", "fee", "contract",
    "negotiate", "negotiating", "negotiation", "buy", "sell", "linked",
    "offer", "agreed", "swap", "move", "depart", "release", "free agent",
    "here we go", "permanent", "option to buy",
    # 日本語
    "移籍", "獲得", "契約", "補強", "放出", "ローン", "売却", "リリース", "移籍金", "交渉", "オファー",
}
_MATCH_KW = {
    "vs", "v.", "match report", "result", "score", "highlights", "defeat",
    "fixture", "matchweek", "matchday", "full-time", "half-time", "kick-off",
    "preview", "line-up", "lineup", "starting xi",
    # 日本語
    "試合", "結果", "スコア", "ハイライト", "引き分け", "プレビュー", "レポート",
    "スターティング", "先発", "キックオフ", "前半", "後半",
}
_EUROPE_KW = {
    "champions league", "europa league", "ucl", "uel", "bundesliga",
    "la liga", "serie a", "ligue 1", "eredivisie", "uefa", "european",
    "champions", "real madrid", "barcelona", "psg", "juventus", "bayern",
    # 日本語
    "チャンピオンズリーグ", "ヨーロッパリーグ", "ブンデスリーガ", "ラ・リーガ", "セリエa",
    "リーグ1", "バルセロナ", "レアル・マドリード", "バイエルン", "ユベントス",
}
_DATA_KW = {
    "data", "stats", "statistics", "xg", "ppda", "expected goals",
    "heatmap", "numbers", "ranking",
    # 日本語
    "データ", "統計", "スタッツ", "ヒートマップ", "ランキング",
}
_TACTICS_KW = {
    "tactic", "tactics", "formation", "system", "pressing",
    "high press", "build-up", "positional play",
    # 日本語
    "戦術", "フォーメーション", "プレッシング", "ハイプレス", "ビルドアップ",
}
_UNITED_KW = {
    "manchester united", "man united", "man utd", "mufc", "old trafford",
    "マンチェスター・ユナイテッド", "マン・ユナイテッド", "マンu", "マンutd",
}


def _is_worldcup_window_active(cfg: dict) -> bool:
    """
    topic_worldcup.active_window で指定された期間内かどうかを判定する。
    未設定の場合は後方互換のため常に有効とみなす。
    """
    window = cfg.get("topic_worldcup", {}).get("active_window")
    if not window:
        return True
    try:
        start = datetime.strptime(window["start"], "%Y-%m-%d").date()
        end = datetime.strptime(window["end"], "%Y-%m-%d").date()
        return start <= datetime.now().date() <= end
    except Exception as e:
        print(f"[main] active_window の解析に失敗、常時有効として扱う: {e}")
        return True


def determine_category_slugs(topic: str) -> list[str]:
    """トピック文字列から該当するカテゴリスラッグをすべて返す（複数可）"""
    lower = topic.lower()
    slugs = []

    if any(kw in lower for kw in _TRANSFER_KW):
        slugs.append("transfers")
    if any(kw in lower for kw in _EUROPE_KW):
        slugs.append("europe")
    if any(kw in lower for kw in _DATA_KW):
        slugs.append("data")
    if any(kw in lower for kw in _TACTICS_KW):
        slugs.append("tactics")
    if any(kw in lower for kw in _MATCH_KW):
        slugs.append("match-reviews")
    if any(kw in lower for kw in _UNITED_KW):
        slugs.append("united")

    return slugs if slugs else ["column"]


def get_category_ids(slugs: list[str], config: dict) -> list[int]:
    cat_ids = config.get("wordpress", {}).get("category_ids", {})
    fallback = config.get("wordpress", {}).get("category_id", 1)
    return [cat_ids.get(s, fallback) for s in slugs]


def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_hash TEXT UNIQUE NOT NULL,
            topic_title TEXT NOT NULL,
            wp_post_id INTEGER,
            wp_url TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analyzed_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER UNIQUE NOT NULL,
            match_title TEXT NOT NULL,
            wp_post_id INTEGER,
            wp_url TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS previewed_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER UNIQUE NOT NULL,
            match_title TEXT NOT NULL,
            wp_post_id INTEGER,
            wp_url TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_dedup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_key TEXT NOT NULL,
            pipeline TEXT NOT NULL,
            created_date TEXT NOT NULL,
            UNIQUE(player_key, pipeline, created_date)
        )
    """)
    # 既存記事の更新方式で使う記事IDの列（旧スキーマのDBには後から追加）
    cols = {r[1] for r in conn.execute("PRAGMA table_info(player_dedup)").fetchall()}
    if "wp_post_id" not in cols:
        conn.execute("ALTER TABLE player_dedup ADD COLUMN wp_post_id INTEGER")
    conn.commit()
    return conn


def is_player_processed_recently(conn: sqlite3.Connection, player_key: str, days: int) -> bool:
    """同じ選手の記事を直近 days 日以内（当日含む）に投稿済みなら True。パイプライン横断で判定する"""
    cutoff = (datetime.now() - timedelta(days=days - 1)).strftime('%Y-%m-%d')
    row = conn.execute(
        "SELECT id FROM player_dedup WHERE player_key=? AND created_date>=?",
        (player_key, cutoff),
    ).fetchone()
    return row is not None


# 移籍系トピック判定（メインパイプラインで選手重複チェックをかける対象）
_TRANSFER_TOPIC_RE = re.compile(
    r"\b(transfer|bid|deal|sign|signing|fee|offer|target|loan|contract|agree|agreed|talks|move|swoop|interest)\b",
    re.IGNORECASE,
)


def mark_player_processed(
    conn: sqlite3.Connection, player_key: str, pipeline: str, post_id: int | None = None
) -> None:
    today = datetime.now().strftime('%Y-%m-%d')
    conn.execute(
        "INSERT OR IGNORE INTO player_dedup (player_key, pipeline, created_date, wp_post_id) VALUES (?, ?, ?, ?)",
        (player_key, pipeline, today, post_id),
    )
    if post_id:
        conn.execute(
            "UPDATE player_dedup SET wp_post_id=? WHERE player_key=? AND pipeline=? AND created_date=?",
            (post_id, player_key, pipeline, today),
        )
    conn.commit()


def find_recent_player_post(conn: sqlite3.Connection, player_key: str, days: int) -> int | None:
    """直近 days 日以内に同じ選手で書いた記事のIDを返す（更新方式の対象）"""
    cutoff = (datetime.now() - timedelta(days=days - 1)).strftime('%Y-%m-%d')
    row = conn.execute(
        "SELECT wp_post_id FROM player_dedup WHERE player_key=? AND created_date>=? AND wp_post_id>0 "
        "ORDER BY created_date DESC, id DESC LIMIT 1",
        (player_key, cutoff),
    ).fetchone()
    return row[0] if row else None


def topic_player_key(title: str, require_transfer: bool = False) -> str | None:
    """選手単位の重複判定・記事更新に使うキー。コメント記事は別の話題になりやすいため対象外"""
    if is_commentary_title(title):
        return None
    if require_transfer and not _TRANSFER_TOPIC_RE.search(title):
        return None
    return extract_player_name(title)


def decide_player_action(conn: sqlite3.Connection, player_key: str | None, cfg: dict) -> tuple[str, int | None]:
    """同じ選手の記事がある場合の扱いを決める。
    ("update", 記事ID): 直近 update_window_days 日以内の既存記事を続報で更新
    ("skip", None):     記事IDの記録がない直近 player_dedup_days 日以内の重複
    ("new", None):      新規記事"""
    if not player_key:
        return "new", None
    art_cfg = cfg.get("article", {})
    post_id = find_recent_player_post(conn, player_key, art_cfg.get("update_window_days", 30))
    if post_id:
        return "update", post_id
    if is_player_processed_recently(conn, player_key, art_cfg.get("player_dedup_days", 7)):
        return "skip", None
    return "new", None


def get_analyzed_match_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT match_id FROM analyzed_matches").fetchall()
    return {r[0] for r in rows}


def mark_match_analyzed(conn: sqlite3.Connection, match_id: int, title: str, post_id: int, post_url: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO analyzed_matches
           (match_id, match_title, wp_post_id, wp_url, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (match_id, title, post_id, post_url, datetime.now().isoformat()),
    )
    conn.commit()


def get_previewed_match_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT match_id FROM previewed_matches").fetchall()
    return {r[0] for r in rows}


def mark_match_previewed(conn: sqlite3.Connection, match_id: int, title: str, post_id: int, post_url: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO previewed_matches
           (match_id, match_title, wp_post_id, wp_url, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (match_id, title, post_id, post_url, datetime.now().isoformat()),
    )
    conn.commit()


def topic_hash(topic: Topic) -> str:
    key = topic.title.lower().strip()
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def is_processed(conn: sqlite3.Connection, topic: Topic) -> bool:
    h = topic_hash(topic)
    row = conn.execute(
        "SELECT id FROM processed_topics WHERE topic_hash = ?", (h,)
    ).fetchone()
    if row:
        return True
    # タイトルの主要単語（5文字以上）が過去14日以内のタイトルと3語以上一致すれば重複とみなす
    words = {w for w in re.sub(r"[^\w\s]", "", topic.title.lower()).split() if len(w) >= 5}
    if not words:
        return False
    cutoff = (datetime.now() - timedelta(days=14)).isoformat()
    recent = conn.execute(
        "SELECT topic_title FROM processed_topics WHERE created_at >= ?", (cutoff,)
    ).fetchall()
    for (title,) in recent:
        past_words = {w for w in re.sub(r"[^\w\s]", "", title.lower()).split() if len(w) >= 5}
        if len(words & past_words) >= 3:
            return True
    return False


def mark_processed(conn: sqlite3.Connection, topic: Topic, post_id: int, post_url: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO processed_topics
           (topic_hash, topic_title, wp_post_id, wp_url, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (topic_hash(topic), topic.title, post_id, post_url, datetime.now().isoformat()),
    )
    conn.commit()


def _with_topic_source(topic: Topic, results: list) -> list:
    """トピック元記事（RSSのリンク）がホワイトリスト内なら主要ソースとして先頭に加える。
    非英語トピック（西語RSS等）は翻訳検索だと無関係な記事しか拾えず、
    AIが SKIP_OLD_NEWS を返して全滅していたため、元記事を必ずソースに含める。"""
    if not topic.url or any(r.url == topic.url for r in results):
        return results
    with open(CONFIG_PATH, encoding="utf-8") as f:
        whitelist = yaml.safe_load(f)["sources"]["whitelist"]
    if not is_whitelisted(topic.url, whitelist) or not _is_article_url(topic.url):
        return results
    print(f"[main] トピック元記事をソースに追加: {topic.url}")
    source = SearchResult(
        title=topic.title,
        url=topic.url,
        snippet="",
        score=1.0,
        published_date=topic.published_date,
    )
    return [source] + results


def _find_valid_topic(
    candidate_topics: list[Topic],
    context: str = "default",
    used_urls: set[str] | None = None,
) -> tuple[Topic | None, list, list]:
    """候補から記事収集に成功した最初のトピックを返す"""
    for candidate in candidate_topics:
        print(f"[main] トピック試行: {candidate.title}")

        _results = search_articles(candidate.title, CONFIG_PATH, context=context)
        _results = _with_topic_source(candidate, _results)
        if len(_results) < MIN_ARTICLES:
            print(f"[main] 検索結果不足 ({len(_results)} 件)、次のトピックへ")
            continue

        # 参照ソースが既採用トピックと大きく重複する場合、同一試合/出来事を
        # 異なる切り口で再記事化しているだけの可能性が高いためスキップする
        if used_urls:
            candidate_urls = {r.url for r in _results}
            overlap = candidate_urls & used_urls
            if len(overlap) >= 2:
                print(f"[main] 既出トピックとソース重複 ({len(overlap)} 件)、同一の出来事の可能性が高いためスキップ")
                continue

        _articles = fetch_articles([r.url for r in _results])

        fetched_urls = {a.url for a in _articles}
        snippet_count = sum(
            1 for r in _results
            if r.url not in fetched_urls and len(r.snippet.split()) >= 20
        )
        effective_sources = len(_articles) + snippet_count

        if effective_sources < MIN_ARTICLES:
            print(f"[main] 有効ソース不足 ({len(_articles)} 記事 + {snippet_count} スニペット)、次のトピックへ")
            continue
        if len(_articles) < 1:
            print(f"[main] フル記事が1件もなし、次のトピックへ")
            continue

        print(f"[main] 有効ソース: {len(_articles)} 記事 + {snippet_count} スニペット = {effective_sources} 件")
        return candidate, _results, _articles

    return None, [], []


def _post_article(
    topic: Topic,
    articles: list,
    search_results: list,
    dry_run: bool,
    conn: sqlite3.Connection,
    cfg: dict,
    force_category: str | None = None,
    context: str = "default",
    update_post_id: int | None = None,
) -> int | None:
    """1記事を生成・投稿する。成功したら記事ID（dry-run は 0）、失敗・スキップは None を返す。
    update_post_id 指定時は既存記事に続報を統合して上書き更新する"""
    existing = fetch_post_for_update(update_post_id, CONFIG_PATH) if update_post_id else None
    if update_post_id and existing is None:
        print(f"[main] 更新対象の記事が取得できないため新規記事として生成 (ID={update_post_id})")
    if existing:
        updated = _update_article(topic, articles, search_results, dry_run, conn, existing)
        if updated != _FALLBACK_TO_NEW:
            return updated

    generated = generate_article(topic.title, articles, search_results, CONFIG_PATH, context=context)

    if generated.content.strip() == "SKIP_OLD_NEWS":
        print(f"[main] 古いニュースのためスキップ: {topic.title}")
        mark_processed(conn, topic, 0, "")  # 再選択されないよう記録
        return None

    if generated.content.strip() == "SKIP_LOW_QUALITY":
        print(f"[main] 生成品質不足のためスキップ（投稿しない）: {topic.title}")
        mark_processed(conn, topic, 0, "")  # 再選択されないよう記録
        return None

    if dry_run:
        print("\n[main] DRY RUN モード - WordPress には投稿しません")
        print("-" * 60)
        print(generated.content[:800])
        print("-" * 60)
        return 0

    # アイキャッチ画像取得・アップロード
    # 英語トピック + ソース記事タイトルを結合して選手名抽出精度を向上
    # ここで使うのは「検索でヒットしただけの記事」ではなく「実際に生成記事内で
    # 引用された記事」のみに限定する。全ヒット記事を混ぜると、記事本文と無関係な
    # 選手名（Man United関連の補完検索で拾った別記事の選手等）が抽出され、
    # 本文の主題とかけ離れたアイキャッチが選ばれることがあるため。
    featured_media_id = None
    img_topic = topic.title
    if articles:
        cited_titles = " ".join(
            a.title for a in articles if a.title and a.url and a.url in generated.content
        )
        if cited_titles:
            img_topic = f"{img_topic} {cited_titles}"
    img_result = fetch_image(img_topic, primary_topic=topic.title)
    if img_result:
        img_bytes, img_filename, img_attribution = img_result
        upload_result = upload_media(img_bytes, img_filename, img_attribution, CONFIG_PATH)
        if upload_result:
            featured_media_id, _ = upload_result

    # 記事内選手写真取得・アップロード
    inline_player_images: list[tuple[str, str, str]] = []
    player_img_data = fetch_player_images(topic.title, max_images=2)
    for p_bytes, p_filename, p_attr, p_name in player_img_data:
        upload_result = upload_media(p_bytes, p_filename, p_attr, CONFIG_PATH)
        if upload_result:
            _, p_url = upload_result
            inline_player_images.append((p_url, p_name, p_attr))

    # カテゴリ判定（force_category が指定されていれば先頭に固定して追加判定も行う）
    auto_slugs = determine_category_slugs(topic.title)
    if force_category:
        cat_slugs = [force_category] + [s for s in auto_slugs if s != force_category]
    else:
        cat_slugs = auto_slugs
    cat_ids = get_category_ids(cat_slugs, cfg)
    print(f"[main] カテゴリ判定: {cat_slugs} (IDs={cat_ids})")

    result = publish_draft(
        generated.title, generated.content, CONFIG_PATH,
        featured_media_id=featured_media_id,
        inline_player_images=inline_player_images or None,
        category_ids=cat_ids,
        meta_description=generated.meta_description,
    )
    mark_processed(conn, topic, result.post_id, result.url)

    print(f"[main] 投稿完了: Post ID={result.post_id}")
    print(f"[main] URL: {result.url}")

    _send_indexnow(result)
    return result.post_id


def _send_indexnow(result) -> None:
    """Bing IndexNowでインデックス促進（draftはURLが404を返すため送信対象外）"""
    indexnow_key = os.environ.get("BING_INDEXNOW_KEY", "")
    if indexnow_key and result.url and result.status == "publish":
        try:
            import requests as _req
            _req.get(
                "https://api.indexnow.org/indexnow",
                params={"url": result.url, "key": indexnow_key},
                timeout=10,
                verify=_SSL_VERIFY,
            )
            print(f"[main] Bing IndexNow送信完了: {result.url}")
        except Exception as e:
            print(f"[main] IndexNow送信失敗（続行）: {e}")


# _update_article が「既存記事とは別の案件」と判定した時の戻り値（新規記事の生成に回す）
_FALLBACK_TO_NEW = -1


def _update_article(
    topic: Topic,
    articles: list,
    search_results: list,
    dry_run: bool,
    conn: sqlite3.Connection,
    existing: dict,
) -> int | None:
    """既存記事に続報を統合して上書き更新する。公開済み記事も公開のまま更新する
    （旧版は WordPress のリビジョンに残るため管理画面から戻せる）"""
    print(f"[main] 既存記事を続報で更新: ID={existing['id']} ({existing['status']}) {existing['title']}")
    existing_md, player_images = html_to_markdown_for_update(existing["html"])
    generated = generate_update_article(
        topic.title, existing["title"], existing_md, existing["date"],
        articles, search_results, CONFIG_PATH,
    )
    if generated.content == "SKIP_DIFFERENT_STORY":
        return _FALLBACK_TO_NEW
    if generated.content in ("SKIP_NO_NEW_FACTS", "SKIP_LOW_QUALITY"):
        mark_processed(conn, topic, 0, "")  # 再選択されないよう記録
        return None

    if dry_run:
        print("\n[main] DRY RUN モード - 既存記事は更新しません")
        print("-" * 60)
        print(generated.content[:1500])
        print("-" * 60)
        return 0

    result = publish_draft(
        generated.title, generated.content, CONFIG_PATH,
        inline_player_images=player_images or None,
        category_ids=existing["categories"],
        meta_description=generated.meta_description,
        update_post_id=existing["id"],
    )
    mark_processed(conn, topic, result.post_id, result.url)
    print(f"[main] 更新完了: Post ID={result.post_id} ({result.status})")
    print(f"[main] URL: {result.url}")
    _send_indexnow(result)
    return result.post_id


class CreditExhaustedError(Exception):
    """Anthropic API のクレジット残高不足。以降の生成は全て失敗するため即中断する"""


def _raise_if_credit_error(e: Exception) -> None:
    if "credit balance is too low" in str(e):
        raise CreditExhaustedError(str(e)) from e


ALERT_FILE = "alert.txt"


def _write_alert(message: str) -> None:
    """CI のメール通知本文用に異常内容を書き出す"""
    with open(ALERT_FILE, "w", encoding="utf-8") as f:
        f.write(message + "\n")


def run(dry_run: bool = False, topic_override: str | None = None, count: int = 1) -> int:
    """全パイプラインの投稿・更新件数を返す"""
    print("=" * 60)
    print(f"[main] Premier Blog 自動投稿開始 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"[main] 生成目標: {count} 記事")
    print("=" * 60)

    # スコアティッカー・順位表・日程更新（失敗してもパイプライン継続）
    if not dry_run:
        try:
            from score_updater import update_ticker
            update_ticker(CONFIG_PATH)
        except Exception as e:
            print(f"[main] スコアティッカー更新失敗（続行）: {e}")

        # 4大リーグデータ更新（レート制限回避のため60秒待ってから実行）
        try:
            import time
            from score_updater import _update_multi_league
            with open(CONFIG_PATH, encoding="utf-8") as _f:
                _cfg = yaml.safe_load(_f)
            _wp_url = os.environ.get("WP_URL", _cfg.get("wordpress", {}).get("url", ""))
            print("[main] 4大リーグ更新待機中（60秒）...")
            time.sleep(60)
            _update_multi_league(_wp_url)
        except Exception as e:
            print(f"[main] 4大リーグデータ更新失敗（続行）: {e}")

    conn = init_db()

    with open(CONFIG_PATH, encoding="utf-8") as _f:
        cfg = yaml.safe_load(_f)

    if topic_override:
        candidate_topics = [Topic(title=topic_override)] * count
    else:
        topics = find_topics(CONFIG_PATH)
        unprocessed = [t for t in topics if not is_processed(conn, t)]
        if not unprocessed:
            print("[main] 新規トピックなし（全て処理済み）")
            conn.close()
            return
        candidate_topics = sorted(unprocessed, key=lambda t: t.score, reverse=True)

    used_hashes: set[str] = set()
    used_urls: set[str] = set()
    success_count = 0
    # パイプラインがスキップされても戻り値で集計できるよう先に初期化
    transfer_success = europe_success = wc_success = longtail_success = extra_success = 0
    dedup_days = cfg.get("article", {}).get("player_dedup_days", 7)

    def _main_player_key(t: Topic) -> str | None:
        # 移籍系トピックのみ選手単位で重複判定する（試合・監督コメント等は対象外）
        return topic_player_key(t.title, require_transfer=True)

    # 同じ実行内で既に更新した記事は再更新しない
    updated_ids: set[int] = set()

    def _player_action(key: str | None) -> tuple[str, int | None]:
        action, pid = decide_player_action(conn, key, cfg)
        if action == "update" and pid in updated_ids:
            return "skip", None
        return action, pid

    def _record_player(key: str | None, pipeline: str, action: str, post_id: int) -> None:
        if not key:
            return
        mark_player_processed(conn, key, pipeline, post_id or None)
        if action == "update" and post_id:
            updated_ids.add(post_id)

    if not topic_override:
        filtered = []
        for t in candidate_topics:
            key = _main_player_key(t)
            if _player_action(key)[0] == "skip":
                print(f"[main] 選手重複スキップ（直近{dedup_days}日に記事化済み）: {key} — {t.title}")
                continue
            filtered.append(t)
        candidate_topics = filtered

    for i in range(count):
        if i > 0:
            print(f"\n{'=' * 60}")
            print(f"[main] 記事 {i + 1}/{count} 開始")
            print("=" * 60)

        # 今回のループで使用済みのトピックを除外（同じ実行内で記事化・更新した選手も除外）
        remaining = [
            t for t in candidate_topics
            if topic_hash(t) not in used_hashes
            and _player_action(_main_player_key(t))[0] != "skip"
        ]
        if not remaining:
            print(f"[main] 残りトピックなし。{success_count}/{count} 記事生成済み")
            break

        topic, search_results, articles = _find_valid_topic(remaining, used_urls=used_urls)

        if topic is None:
            print(f"[main] 全トピックで記事収集に失敗。{success_count}/{count} 記事生成済み")
            break

        used_hashes.add(topic_hash(topic))
        used_urls.update(r.url for r in search_results)
        print(f"[main] 採用トピック: {topic.title}")
        main_key = _main_player_key(topic)
        action, target_id = _player_action(main_key)

        try:
            post_id = _post_article(topic, articles, search_results, dry_run, conn, cfg, update_post_id=target_id)
            if post_id is not None:
                success_count += 1
                if not dry_run:
                    _record_player(main_key, "main", action, post_id)
        except Exception as e:
            _raise_if_credit_error(e)
            print(f"[main] 記事生成・投稿エラー: {e}")
            # エラーがあっても次のトピックへ進む
            continue

    print("\n" + "=" * 60)
    print(f"[main] 完了: {success_count}/{count} 記事を生成しました")
    print("=" * 60)

    # ===== 移籍記事パイプライン（Man United 以外） =====
    count_transfers = cfg.get("topic_transfers", {}).get("count", 2)
    if count_transfers > 0 and not topic_override:
        print("\n" + "=" * 60)
        print(f"[main] 移籍記事を {count_transfers} 件生成します（MU以外）")
        print("=" * 60)
        transfer_topics = find_topics_transfers(CONFIG_PATH)
        transfer_unprocessed = [t for t in transfer_topics if not is_processed(conn, t) and topic_hash(t) not in used_hashes]
        transfer_success = 0
        for i in range(count_transfers):
            remaining = [t for t in transfer_unprocessed if topic_hash(t) not in used_hashes]
            if not remaining:
                print(f"[main] 移籍トピックなし。{transfer_success}/{count_transfers} 記事生成済み")
                break
            topic, search_results, articles = _find_valid_topic(remaining, context="transfers", used_urls=used_urls)
            if topic is None:
                print(f"[main] 移籍: 全トピックで記事収集失敗")
                break
            used_hashes.add(topic_hash(topic))
            used_urls.update(r.url for r in search_results)

            # 直近に同じ選手の記事があれば既存記事を更新、記事IDが無ければスキップ（パイプライン横断）
            player_key = topic_player_key(topic.title)
            action, target_id = _player_action(player_key)
            if action == "skip":
                print(f"[main] 移籍: 選手重複スキップ ({player_key}、直近{dedup_days}日)")
                continue

            print(f"[main] 移籍採用トピック: {topic.title}")
            try:
                post_id = _post_article(
                    topic, articles, search_results, dry_run, conn, cfg,
                    force_category="transfers", update_post_id=target_id,
                )
                if post_id is not None:
                    transfer_success += 1
                    _record_player(player_key, "transfers", action, post_id)
            except Exception as e:
                _raise_if_credit_error(e)
                print(f"[main] 移籍記事エラー: {e}")
        print(f"[main] 移籍記事完了: {transfer_success}/{count_transfers} 件")

    # ===== 欧州記事パイプライン（プレミアリーグ以外） =====
    count_europe = cfg.get("topic_europe", {}).get("count", 2)
    if count_europe > 0 and not topic_override:
        print("\n" + "=" * 60)
        print(f"[main] 欧州記事を {count_europe} 件生成します（PL以外）")
        print("=" * 60)
        europe_topics = find_topics_europe(CONFIG_PATH)
        europe_unprocessed = [t for t in europe_topics if not is_processed(conn, t) and topic_hash(t) not in used_hashes]
        europe_success = 0
        for i in range(count_europe):
            remaining = [t for t in europe_unprocessed if topic_hash(t) not in used_hashes]
            if not remaining:
                print(f"[main] 欧州トピックなし。{europe_success}/{count_europe} 記事生成済み")
                break
            topic, search_results, articles = _find_valid_topic(remaining, context="europe", used_urls=used_urls)
            if topic is None:
                print(f"[main] 欧州: 全トピックで記事収集失敗")
                break
            used_hashes.add(topic_hash(topic))
            used_urls.update(r.url for r in search_results)

            # パイプライン横断で直近の同選手は既存記事を更新、記事IDが無ければスキップ
            player_key = topic_player_key(topic.title)
            action, target_id = _player_action(player_key)
            if action == "skip":
                print(f"[main] 欧州: 選手重複スキップ ({player_key}、直近{dedup_days}日)")
                continue

            print(f"[main] 欧州採用トピック: {topic.title}")
            try:
                post_id = _post_article(
                    topic, articles, search_results, dry_run, conn, cfg,
                    force_category="europe", update_post_id=target_id,
                )
                if post_id is not None:
                    europe_success += 1
                    _record_player(player_key, "europe", action, post_id)
            except Exception as e:
                _raise_if_credit_error(e)
                print(f"[main] 欧州記事エラー: {e}")
        print(f"[main] 欧州記事完了: {europe_success}/{count_europe} 件")

    # ===== ワールドカップ MU選手記事パイプライン =====
    count_wc = cfg.get("topic_worldcup", {}).get("count", 2)
    if count_wc > 0 and not topic_override and not _is_worldcup_window_active(cfg):
        print("[main] W杯企画: active_window 外のためスキップ")
        count_wc = 0
    if count_wc > 0 and not topic_override:
        print("\n" + "=" * 60)
        print(f"[main] WC記事を {count_wc} 件生成します（MU選手のW杯活躍）")
        print("=" * 60)
        from topic_finder import find_topics_worldcup
        wc_topics = find_topics_worldcup(CONFIG_PATH)
        wc_unprocessed = [t for t in wc_topics if not is_processed(conn, t) and topic_hash(t) not in used_hashes]
        wc_success = 0
        for i in range(count_wc):
            remaining = [t for t in wc_unprocessed if topic_hash(t) not in used_hashes]
            if not remaining:
                print(f"[main] WCトピックなし。{wc_success}/{count_wc} 記事生成済み")
                break
            topic, search_results, articles = _find_valid_topic(remaining, context="worldcup")
            if topic is None:
                print(f"[main] WC: 全トピックで記事収集失敗")
                break
            used_hashes.add(topic_hash(topic))

            player_key = topic_player_key(topic.title)
            action, target_id = _player_action(player_key)
            if action == "skip":
                print(f"[main] WC: 選手重複スキップ ({player_key}、直近{dedup_days}日)")
                continue

            print(f"[main] WC採用トピック: {topic.title}")
            try:
                post_id = _post_article(
                    topic, articles, search_results, dry_run, conn, cfg,
                    force_category="united", update_post_id=target_id,
                )
                if post_id is not None:
                    wc_success += 1
                    _record_player(player_key, "worldcup", action, post_id)
            except Exception as e:
                _raise_if_credit_error(e)
                print(f"[main] WC記事エラー: {e}")
        print(f"[main] WC記事完了: {wc_success}/{count_wc} 件")

    # ===== ロングテールキーワード記事パイプライン =====
    count_longtail = cfg.get("topic_longtail", {}).get("count", 1)
    if count_longtail > 0 and not topic_override:
        print("\n" + "=" * 60)
        print(f"[main] ロングテール記事を {count_longtail} 件生成します")
        print("=" * 60)
        from topic_finder import find_topics_longtail
        longtail_topics = find_topics_longtail(CONFIG_PATH)
        longtail_unprocessed = [t for t in longtail_topics if not is_processed(conn, t) and topic_hash(t) not in used_hashes]
        longtail_success = 0
        for i in range(count_longtail):
            remaining = [t for t in longtail_unprocessed if topic_hash(t) not in used_hashes]
            if not remaining:
                print(f"[main] ロングテールクエリなし（在庫消化済み）。{longtail_success}/{count_longtail} 記事生成済み")
                break
            topic, search_results, articles = _find_valid_topic(remaining, context="longtail")
            if topic is None:
                print(f"[main] ロングテール: 全クエリで記事収集失敗")
                break
            used_hashes.add(topic_hash(topic))

            print(f"[main] ロングテール採用クエリ: {topic.title} (カテゴリ: {topic.category})")
            try:
                post_id = _post_article(topic, articles, search_results, dry_run, conn, cfg, force_category=topic.category, context="longtail")
                if post_id is not None:
                    longtail_success += 1
            except Exception as e:
                _raise_if_credit_error(e)
                print(f"[main] ロングテール記事エラー: {e}")
        print(f"[main] ロングテール記事完了: {longtail_success}/{count_longtail} 件")

    # 試合分析記事（dry_run 時はスキップ）
    if not dry_run:
        try:
            from match_analyzer import find_analysis_match, generate_analysis_article
            analyzed_ids = get_analyzed_match_ids(conn)
            match = find_analysis_match(analyzed_ids)
            if match:
                print("\n" + "=" * 60)
                print("[main] 試合分析記事を生成します")
                print("=" * 60)
                generated = generate_analysis_article(match, CONFIG_PATH)
                if generated:
                    cat_id = get_category_ids(["match-reviews"], cfg)[0]
                    img_result = fetch_image(match.get("homeTeam", {}).get("name", "") + " football match")
                    featured_media_id = None
                    if img_result:
                        img_bytes, img_filename, img_attribution = img_result
                        upload_result = upload_media(img_bytes, img_filename, img_attribution, CONFIG_PATH)
                        if upload_result:
                            featured_media_id, _ = upload_result
                    result = publish_draft(
                        generated.title, generated.content, CONFIG_PATH,
                        featured_media_id=featured_media_id,
                        category_id=cat_id,
                        meta_description=generated.meta_description,
                    )
                    mark_match_analyzed(conn, match["id"], generated.title, result.post_id, result.url)
                    extra_success += 1
                    print(f"[main] 分析記事投稿完了: Post ID={result.post_id}")
                    print(f"[main] URL: {result.url}")
            else:
                print("[main] 分析対象の試合なし（直近5日に未分析の完了試合がない）")
        except Exception as e:
            _raise_if_credit_error(e)
            print(f"[main] 分析記事生成エラー（続行）: {e}")

    # マンU戦プレビュー記事（dry_run 時はスキップ）
    if not dry_run:
        try:
            from match_analyzer import find_preview_match, generate_preview_article
            previewed_ids = get_previewed_match_ids(conn)
            preview_match = find_preview_match(previewed_ids)
            if preview_match:
                print("\n" + "=" * 60)
                print("[main] マンU戦プレビュー記事を生成します")
                print("=" * 60)
                generated = generate_preview_article(preview_match, CONFIG_PATH)
                if generated:
                    cat_id = get_category_ids(["united"], cfg)[0]
                    home_name = preview_match.get("homeTeam", {}).get("name", "")
                    away_name = preview_match.get("awayTeam", {}).get("name", "")
                    img_result = fetch_image(f"{home_name} {away_name} football match")
                    featured_media_id = None
                    if img_result:
                        img_bytes, img_filename, img_attribution = img_result
                        upload_result = upload_media(img_bytes, img_filename, img_attribution, CONFIG_PATH)
                        if upload_result:
                            featured_media_id, _ = upload_result
                    result = publish_draft(
                        generated.title, generated.content, CONFIG_PATH,
                        featured_media_id=featured_media_id,
                        category_id=cat_id,
                        meta_description=generated.meta_description,
                    )
                    mark_match_previewed(conn, preview_match["id"], generated.title, result.post_id, result.url)
                    extra_success += 1
                    print(f"[main] プレビュー記事投稿完了: Post ID={result.post_id}")
                    print(f"[main] URL: {result.url}")
            else:
                print("[main] プレビュー対象のマンU戦なし（直近2日以内に予定なし）")
        except Exception as e:
            _raise_if_credit_error(e)
            print(f"[main] プレビュー記事生成エラー（続行）: {e}")

    conn.close()
    return success_count + transfer_success + europe_success + wc_success + longtail_success + extra_success


def main() -> None:
    parser = argparse.ArgumentParser(description="Premier League 自動ブログ投稿")
    parser.add_argument("--dry-run", action="store_true", help="WordPress に投稿せずに記事内容を確認")
    parser.add_argument("--topic", type=str, default=None, help="テーマを直接指定")
    parser.add_argument("--count", type=int, default=5, help="生成する記事数（デフォルト: 5）")
    args = parser.parse_args()

    try:
        total = run(dry_run=args.dry_run, topic_override=args.topic, count=args.count)
    except CreditExhaustedError as e:
        msg = f"Anthropic API のクレジット残高不足で記事生成を中断した。\nConsole の Plans & Billing でチャージが必要。\n\n{e}"
        print(f"[main] {msg}")
        _write_alert(msg)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[main] 中断されました")
        sys.exit(0)
    except Exception as e:
        print(f"[main] エラー: {e}")
        _write_alert(f"main.py が例外で異常終了した。\n\n{e}")
        raise

    if total == 0 and not args.dry_run:
        msg = "全パイプラインで投稿・更新が0件だった。ログで原因を確認すること。"
        print(f"[main] {msg}")
        _write_alert(msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
