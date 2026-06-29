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
from publisher import publish_draft, upload_media, insert_player_images
from researcher import search_articles
from synthesizer import generate_article
from topic_finder import Topic, find_topics, find_topics_transfers, find_topics_europe, select_topic

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
        CREATE TABLE IF NOT EXISTS player_dedup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_key TEXT NOT NULL,
            pipeline TEXT NOT NULL,
            created_date TEXT NOT NULL,
            UNIQUE(player_key, pipeline, created_date)
        )
    """)
    conn.commit()
    return conn


def extract_player_name(title: str) -> str | None:
    """移籍記事タイトルから選手名を抽出（先頭の大文字連続語2〜3語）"""
    # [account] プレフィックスを除去
    title = re.sub(r'^\[[^\]]+\]\s*', '', title)
    words = title.split()
    name_parts = []
    for word in words[:5]:
        clean = re.sub(r'[^a-zA-Z\-]', '', word)
        if clean and clean[0].isupper() and len(clean) > 1:
            name_parts.append(clean)
        else:
            break
    return ' '.join(name_parts[:3]).lower() if len(name_parts) >= 2 else None


def is_player_processed_today(conn: sqlite3.Connection, player_key: str, pipeline: str | None = None) -> bool:
    today = datetime.now().strftime('%Y-%m-%d')
    if pipeline:
        row = conn.execute(
            "SELECT id FROM player_dedup WHERE player_key=? AND pipeline=? AND created_date=?",
            (player_key, pipeline, today),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM player_dedup WHERE player_key=? AND created_date=?",
            (player_key, today),
        ).fetchone()
    return row is not None


def mark_player_processed(conn: sqlite3.Connection, player_key: str, pipeline: str) -> None:
    today = datetime.now().strftime('%Y-%m-%d')
    conn.execute(
        "INSERT OR IGNORE INTO player_dedup (player_key, pipeline, created_date) VALUES (?, ?, ?)",
        (player_key, pipeline, today),
    )
    conn.commit()


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


def _find_valid_topic(
    candidate_topics: list[Topic],
    context: str = "default",
) -> tuple[Topic | None, list, list]:
    """候補から記事収集に成功した最初のトピックを返す"""
    for candidate in candidate_topics:
        print(f"[main] トピック試行: {candidate.title}")

        _results = search_articles(candidate.title, CONFIG_PATH, context=context)
        if len(_results) < MIN_ARTICLES:
            print(f"[main] 検索結果不足 ({len(_results)} 件)、次のトピックへ")
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
) -> bool:
    """1記事を生成・投稿する。成功したら True を返す"""
    generated = generate_article(topic.title, articles, search_results, CONFIG_PATH)

    if generated.content.strip() == "SKIP_OLD_NEWS":
        print(f"[main] 古いニュースのためスキップ: {topic.title}")
        mark_processed(conn, topic, 0, "")  # 再選択されないよう記録
        return False

    if dry_run:
        print("\n[main] DRY RUN モード - WordPress には投稿しません")
        print("-" * 60)
        print(generated.content[:800])
        print("-" * 60)
        return True

    # アイキャッチ画像取得・アップロード
    # 英語トピック + ソース記事タイトルを結合して選手名抽出精度を向上
    featured_media_id = None
    img_topic = topic.title
    if articles:
        src_titles = " ".join(a.title for a in articles if a.title)
        img_topic = f"{img_topic} {src_titles}"
    img_result = fetch_image(img_topic)
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

    # Bing IndexNowでインデックス促進
    indexnow_key = os.environ.get("BING_INDEXNOW_KEY", "")
    if indexnow_key and result.url:
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

    return True


def run(dry_run: bool = False, topic_override: str | None = None, count: int = 1) -> None:
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
    success_count = 0

    for i in range(count):
        if i > 0:
            print(f"\n{'=' * 60}")
            print(f"[main] 記事 {i + 1}/{count} 開始")
            print("=" * 60)

        # 今回のループで使用済みのトピックを除外
        remaining = [t for t in candidate_topics if topic_hash(t) not in used_hashes]
        if not remaining:
            print(f"[main] 残りトピックなし。{success_count}/{count} 記事生成済み")
            break

        topic, search_results, articles = _find_valid_topic(remaining)

        if topic is None:
            print(f"[main] 全トピックで記事収集に失敗。{success_count}/{count} 記事生成済み")
            break

        used_hashes.add(topic_hash(topic))
        print(f"[main] 採用トピック: {topic.title}")

        try:
            _post_article(topic, articles, search_results, dry_run, conn, cfg)
            success_count += 1
        except Exception as e:
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
            topic, search_results, articles = _find_valid_topic(remaining, context="transfers")
            if topic is None:
                print(f"[main] 移籍: 全トピックで記事収集失敗")
                break
            used_hashes.add(topic_hash(topic))

            # 同日に同じ選手の移籍記事がすでにあればスキップ
            player_key = extract_player_name(topic.title)
            if player_key and is_player_processed_today(conn, player_key, "transfers"):
                print(f"[main] 移籍: 選手重複スキップ ({player_key})")
                continue

            print(f"[main] 移籍採用トピック: {topic.title}")
            try:
                ok = _post_article(topic, articles, search_results, dry_run, conn, cfg, force_category="transfers")
                if ok:
                    transfer_success += 1
                    if player_key:
                        mark_player_processed(conn, player_key, "transfers")
            except Exception as e:
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
            topic, search_results, articles = _find_valid_topic(remaining, context="europe")
            if topic is None:
                print(f"[main] 欧州: 全トピックで記事収集失敗")
                break
            used_hashes.add(topic_hash(topic))

            # パイプライン横断で同日同選手スキップ
            player_key = extract_player_name(topic.title)
            if player_key and is_player_processed_today(conn, player_key):
                print(f"[main] 欧州: 選手重複スキップ ({player_key})")
                continue

            print(f"[main] 欧州採用トピック: {topic.title}")
            try:
                ok = _post_article(topic, articles, search_results, dry_run, conn, cfg, force_category="europe")
                if ok and player_key:
                    mark_player_processed(conn, player_key, "europe")
                if ok:
                    europe_success += 1
            except Exception as e:
                print(f"[main] 欧州記事エラー: {e}")
        print(f"[main] 欧州記事完了: {europe_success}/{count_europe} 件")

    # ===== ワールドカップ MU選手記事パイプライン =====
    count_wc = cfg.get("topic_worldcup", {}).get("count", 2)
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

            player_key = extract_player_name(topic.title)
            if player_key and is_player_processed_today(conn, player_key):
                print(f"[main] WC: 選手重複スキップ ({player_key})")
                continue

            print(f"[main] WC採用トピック: {topic.title}")
            try:
                ok = _post_article(topic, articles, search_results, dry_run, conn, cfg, force_category="united")
                if ok and player_key:
                    mark_player_processed(conn, player_key, "worldcup")
                if ok:
                    wc_success += 1
            except Exception as e:
                print(f"[main] WC記事エラー: {e}")
        print(f"[main] WC記事完了: {wc_success}/{count_wc} 件")

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
                    cat_id = get_category_id("match-reviews", cfg)
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
                    print(f"[main] 分析記事投稿完了: Post ID={result.post_id}")
                    print(f"[main] URL: {result.url}")
            else:
                print("[main] 分析対象の試合なし（直近5日に未分析の完了試合がない）")
        except Exception as e:
            print(f"[main] 分析記事生成エラー（続行）: {e}")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Premier League 自動ブログ投稿")
    parser.add_argument("--dry-run", action="store_true", help="WordPress に投稿せずに記事内容を確認")
    parser.add_argument("--topic", type=str, default=None, help="テーマを直接指定")
    parser.add_argument("--count", type=int, default=5, help="生成する記事数（デフォルト: 5）")
    args = parser.parse_args()

    try:
        run(dry_run=args.dry_run, topic_override=args.topic, count=args.count)
    except KeyboardInterrupt:
        print("\n[main] 中断されました")
        sys.exit(0)
    except Exception as e:
        print(f"[main] エラー: {e}")
        raise


if __name__ == "__main__":
    main()
