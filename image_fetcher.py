"""
アイキャッチ画像・記事内選手画像取得モジュール
Wikimedia Commons API でCC画像を検索・ダウンロード。
見つからない場合は Pexels にフォールバック。
"""

import os
import random
import re
import sqlite3
import requests
import yaml
from dotenv import load_dotenv

load_dotenv()

_SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() != "false"
_WK_UA = "premier-blog/1.0 (https://premier-blog.com; katoshin1978@gmail.com)"

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
PEXELS_API = "https://api.pexels.com/v1/search"
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB 超はWPアップロードでタイムアウトしやすい
DB_PATH = "processed.db"

# 汎用すぎてどの記事にも使われてしまうファイルタイトルのキーワード
_BLOCKED_TITLE_KEYWORDS = {
    "fence", "through fence", "watching through", "spectators fence",
    "fans fence", "children fence", "boys fence", "kids watching",
    "fans watching", "supporters watching", "crowd fence",
    "through the fence", "outside stadium", "outside ground",
    "sierra leone", "covid-19 ban", "football devotees", "northern sierra",
    "watch premier league games", "climbed stirs",
}

# キーワード → 表示用チーム名（選手写真クエリに使用）
_TEAM_DISPLAY_NAMES: dict[str, str] = {
    "manchester united": "Manchester United",
    "man united": "Manchester United",
    "man utd": "Manchester United",
    "mufc": "Manchester United",
    "old trafford": "Manchester United",
    "red devils": "Manchester United",
    "ruben amorim": "Manchester United",
    "amorim": "Manchester United",
    "arsenal": "Arsenal",
    "chelsea": "Chelsea",
    "liverpool": "Liverpool",
    "manchester city": "Manchester City",
    "man city": "Manchester City",
    "tottenham": "Tottenham",
    "spurs": "Tottenham",
    "newcastle": "Newcastle United",
    "aston villa": "Aston Villa",
    "sunderland": "Sunderland",
    "brighton": "Brighton",
    "west ham": "West Ham",
    "everton": "Everton",
}

# キーワード → Wikimedia Commons アイキャッチ用検索クエリ（横長試合写真向け）
_TEAM_QUERIES: dict[str, str] = {
    "manchester united": "Manchester United football match",
    "man united": "Manchester United football match",
    "man utd": "Manchester United football match",
    "mufc": "Manchester United football match",
    "old trafford": "Old Trafford Manchester United",
    "red devils": "Manchester United football match",
    "ruben amorim": "Manchester United football match",
    "amorim": "Manchester United football match",
    "arsenal": "Arsenal football match Premier League",
    "chelsea": "Chelsea FC football match",
    "liverpool": "Liverpool FC football match",
    "manchester city": "Manchester City football match",
    "man city": "Manchester City football match",
    "tottenham": "Tottenham Hotspur football match",
    "spurs": "Tottenham Hotspur football match",
    "newcastle": "Newcastle United football match",
    "aston villa": "Aston Villa football match",
    "sunderland": "Sunderland AFC football match",
    "brighton": "Brighton Hove Albion football match",
    "west ham": "West Ham United football match",
    "everton": "Everton FC football match",
}

# 名前抽出から除外する語
_EXCLUDE_WORDS = {
    "premier", "league", "united", "city", "arsenal", "chelsea", "liverpool",
    "tottenham", "newcastle", "sunderland", "brighton", "everton", "fulham",
    "brentford", "wolves", "wolverhampton", "ipswich", "bournemouth", "leicester",
    "man", "utd", "mufc", "spurs", "villa", "forest", "palace",
    "manchester", "london", "england", "france", "spain", "germany",
    "transfer", "news", "latest", "update", "report", "deal", "move", "bid",
    "summer", "window", "season", "league", "cup", "final", "semi",
    "match", "game", "draw", "win", "loss", "defeat", "victory",
    "manager", "boss", "coach", "head", "director", "chief",
    "star", "ace", "legend", "icon", "hero", "flop", "wonder",
    "big", "huge", "major", "key", "top", "new", "old", "real", "next",
    "why", "how", "all", "two", "one", "back", "set", "could", "would",
    "after", "before", "over", "into", "with", "from", "amid",
    "despite", "without", "makes", "gives", "urges", "slammed",
    "claims", "claim", "verdict", "decision", "interest", "hints", "admits",
    "confirms", "reveals", "says", "told", "backs", "calls", "wants",
    "snubs", "rejects", "signs", "joins", "leaves", "quits", "returns",
    "football", "soccer", "sport", "bbc", "sky", "guardian", "metro",
    "mood", "turns", "slot", "doku", "shines", "ready", "demands",
    "swift", "clarity", "attract", "signings", "urged", "sell", "weak",
    "goalless", "play", "out", "hits", "misses", "european",
    # 新聞・メディア特有のセクション見出し語（偽の人名として抽出されるのを防ぐ）
    "papers", "exclusive", "breaking", "official", "sources", "report",
    "reports", "transfer", "rumours", "rumors", "daily", "sunday",
    "morning", "evening", "tonight", "today", "yesterday",
    # 文頭に来やすい英単語（疑問詞・冠詞・代名詞など）
    "what", "when", "where", "which", "while", "this", "that", "these",
    "those", "then", "there", "their", "they", "them", "with", "will",
    "has", "have", "had", "the", "and", "but", "for", "not", "are",
    "was", "were", "been", "being", "its", "his", "her", "our", "your",
    "here", "also", "just", "even", "still", "only", "both", "such",
    "said", "says", "show", "shows", "take", "took", "come", "came",
    "keep", "kept", "make", "made", "give", "gave", "look", "looks",
    "think", "thought", "know", "knew", "need", "needs", "needed",
    "like", "liked", "feel", "felt", "find", "found", "turn", "turned",
    "play", "played", "played", "plays", "playing", "scored", "score",
    "miss", "missed", "hit", "hits", "hope", "hopes", "hoped",
    "open", "close", "clear", "help", "helped", "helps",
    "amid", "round", "past", "plus", "less", "more", "most", "much",
    "well", "good", "best", "poor", "away", "home", "away",
    "dressing", "room", "after", "about", "against", "around",
    "beyond", "because", "between", "though", "through", "towards",
}


# -----------------------------------------------------------------------
# 使用済み画像トラッキング（実行内メモリ + processed.db の2重管理）
# -----------------------------------------------------------------------

# 同一実行内の重複をメモリで即時排除（DBコミットタイミングに依存しない）
_used_this_run: set[str] = set()


def _init_image_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS used_images (
            filename TEXT PRIMARY KEY,
            used_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    # 起動時にDB済みのファイル名をメモリに読み込む
    for (fn,) in conn.execute("SELECT filename FROM used_images"):
        _used_this_run.add(fn)
    return conn


# DB接続は1回だけ開いてモジュール内で使いまわす
_db_conn: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        _db_conn = _init_image_db()
    return _db_conn


def _is_image_used(filename: str) -> bool:
    """メモリキャッシュで即時判定（DB不要）"""
    return filename in _used_this_run


def _mark_image_used(filename: str) -> None:
    from datetime import datetime
    _used_this_run.add(filename)
    conn = _get_db()
    conn.execute(
        "INSERT OR IGNORE INTO used_images (filename, used_at) VALUES (?, ?)",
        (filename, datetime.utcnow().isoformat())
    )
    conn.commit()


def _is_blocked_title(title: str) -> bool:
    # Wikimediaのファイル名はスペースが_になるため正規化してから照合
    tl = title.lower().replace("_", " ")
    return any(kw in tl for kw in _BLOCKED_TITLE_KEYWORDS)


# -----------------------------------------------------------------------
# 内部ユーティリティ
# -----------------------------------------------------------------------

def _extract_all_player_names(text: str) -> list[str]:
    """
    テキスト中のTitle Case固有名詞を順番に抽出してペア（名 + 姓）のリストを返す。
    同じ人物が重複しないよう lower() でユニーク化する。
    """
    text = re.sub(r"^\[.*?\]\s*", "", text)
    text = re.sub(r"['''""][^'''""]*['''""]", "", text)
    words = re.findall(r"\b[A-Z][a-z]{2,}\b", text)
    candidates = [w for w in words if w.lower() not in _EXCLUDE_WORDS]

    pairs: list[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(candidates):
        if i + 1 < len(candidates):
            w1, w2 = candidates[i], candidates[i + 1]
            # 両語ともに4文字以上の場合のみ姓名ペアとみなす
            if len(w1) >= 4 and len(w2) >= 4:
                pair = f"{w1} {w2}"
                key = pair.lower()
                if key not in seen:
                    seen.add(key)
                    pairs.append(pair)
                i += 2
            else:
                # 片方が短い場合は単語単位で処理
                if len(w1) >= 5:
                    key = w1.lower()
                    if key not in seen:
                        seen.add(key)
                        pairs.append(w1)
                i += 1
        else:
            solo = candidates[i]
            if len(solo) >= 5:
                key = solo.lower()
                if key not in seen:
                    seen.add(key)
                    pairs.append(solo)
            i += 1
    return pairs


def _wikimedia_get(session: requests.Session, params: dict) -> dict:
    resp = session.get(
        WIKIMEDIA_API,
        headers={"User-Agent": _WK_UA},
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _download(session: requests.Session, url: str) -> bytes | None:
    try:
        r = session.get(url, headers={"User-Agent": _WK_UA}, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"[image_fetcher] ダウンロード失敗: {e}")
        return None


def _make_filename(title: str) -> str:
    raw = title.replace("File:", "")
    raw = re.sub(r"\.(jpe?g|png)$", "", raw, flags=re.I)
    # 非ASCII文字（キリル文字等）を除去してWPアップロードのlatin-1エラーを防ぐ
    raw = raw.encode("ascii", errors="ignore").decode("ascii")
    return re.sub(r"[^\w.-]", "_", raw) + ".jpg"


def _get_attribution(meta: dict) -> str:
    artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
    lic = meta.get("LicenseShortName", {}).get("value", "CC")
    return f"{artist} / Wikimedia Commons ({lic})" if artist else f"Wikimedia Commons ({lic})"


# -----------------------------------------------------------------------
# アイキャッチ画像（横長・試合写真）
# -----------------------------------------------------------------------

def _search_wikimedia_landscape(
    session: requests.Session, query: str
) -> tuple[bytes, str, str] | None:
    """横長（幅800px以上、幅/高さ≥1.2）の試合写真を取得する"""
    try:
        data = _wikimedia_get(session, {
            "action": "query", "generator": "search",
            "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": 30,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiextmetadatafilter": "Artist|LicenseShortName",
            "format": "json",
        })
    except Exception as e:
        print(f"[image_fetcher] Wikimedia 検索失敗 '{query}': {e}")
        return None

    candidates = []
    for page in data.get("query", {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        w, h = info.get("width", 0), info.get("height", 0)
        if w < 800 or h < 100 or (h and w / h < 1.2):
            continue
        candidates.append((w, info, page))

    if not candidates:
        return None

    # 上位10件をシャッフルして順に試す（サイズ上限・ブロック・使用済みをチェック）
    candidates.sort(key=lambda x: x[0], reverse=True)
    top = candidates[:10]
    random.shuffle(top)
    _get_db()  # 初回呼び出しでDBとメモリキャッシュを初期化
    for _, info, page in top:
        title = page.get("title", "image")
        if _is_blocked_title(title):
            print(f"[image_fetcher] スキップ（ブロックリスト）: {title}")
            continue
        filename = _make_filename(title)
        if _is_image_used(filename):
            print(f"[image_fetcher] スキップ（使用済み）: {filename}")
            continue
        content = _download(session, info["url"])
        if not content:
            continue
        if len(content) > MAX_IMAGE_BYTES:
            print(f"[image_fetcher] スキップ（{len(content)//1024}KB 超過）: {title}")
            continue
        attribution = _get_attribution(info.get("extmetadata", {}))
        _mark_image_used(filename)
        print(f"[image_fetcher] アイキャッチ: {filename} ({len(content)//1024}KB) query='{query}'")
        return content, filename, attribution

    return None


def _search_pexels(
    session: requests.Session, api_key: str, query: str
) -> tuple[bytes, str, str] | None:
    try:
        resp = session.get(
            PEXELS_API,
            headers={"Authorization": api_key},
            params={"query": query, "per_page": 15, "orientation": "landscape"},
            timeout=15,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
        if not photos:
            return None
        random.shuffle(photos)
        _get_db()  # 初回呼び出しでDBとメモリキャッシュを初期化
        for photo in photos:
            filename = f"pexels_{photo['id']}.jpg"
            if _is_image_used(filename):
                print(f"[image_fetcher] Pexels スキップ（使用済み）: {filename}")
                continue
            content = _download(session, photo["src"]["large2x"])
            if not content or len(content) > MAX_IMAGE_BYTES:
                continue
            photographer = photo.get("photographer", "Pexels")
            _mark_image_used(filename)
            print(f"[image_fetcher] Pexels fallback: {filename}")
            return content, filename, f"{photographer} / Pexels"
        return None
    except Exception as e:
        print(f"[image_fetcher] Pexels 失敗: {e}")
        return None


def _is_illustration_enabled(config_path: str = "config.yaml") -> bool:
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return bool(cfg.get("illustration", {}).get("enabled", False))
    except Exception:
        return False


def fetch_image(topic: str, config_path: str = "config.yaml") -> tuple[bytes, str, str] | None:
    """
    アイキャッチ用の横長画像を取得する。
    illustration.enabled=true の場合は Flux でイラスト生成。
    それ以外は Wikimedia Commons → Pexels の順でフォールバック。
    Returns (image_bytes, filename, attribution) or None.
    """
    if _is_illustration_enabled(config_path):
        from image_converter import generate_featured_image
        result = generate_featured_image(topic)
        if result:
            return result
        print("[image_fetcher] イラスト生成失敗 → 写真にフォールバック")

    session = requests.Session()
    session.verify = _SSL_VERIFY

    tl = topic.lower()
    team_query = next((q for kw, q in _TEAM_QUERIES.items() if kw in tl), None)
    players = _extract_all_player_names(topic)
    # 2語（姓名）のペアのみアイキャッチクエリに使用（単語だけでは誤検出しやすいため）
    player = next((p for p in players if " " in p), None)

    queries = []
    if player:
        queries.append(f"{player} football")
    if team_query:
        queries.append(team_query)
    queries.append("Premier League football match action")

    for query in queries:
        result = _search_wikimedia_landscape(session, query)
        if result:
            return result

    pexels_key = os.environ.get("PEXELS_API_KEY", "")
    if pexels_key:
        print("[image_fetcher] Wikimedia で画像なし → Pexels")
        pexels_queries = queries + ["soccer football stadium", "football match crowd"]
        for pq in pexels_queries:
            result = _search_pexels(session, pexels_key, pq)
            if result:
                return result

    # 最終フォールバック: 汎用クエリでWikimedia再試行
    for fallback_q in ["Association football", "football stadium", "soccer match"]:
        result = _search_wikimedia_landscape(session, fallback_q)
        if result:
            print(f"[image_fetcher] 最終フォールバック画像使用: {fallback_q}")
            return result

    return None


# -----------------------------------------------------------------------
# 記事内選手画像（縦長・個人写真）
# -----------------------------------------------------------------------

def _search_wikimedia_player(
    session: requests.Session, player_name: str, team: str = ""
) -> tuple[bytes, str, str] | None:
    """選手個人の写真（縦長 or ほぼ正方形、400px以上）を取得する"""
    queries = []
    if team:
        queries.append(f"{player_name} {team}")   # 例: "Bruno Fernandes Manchester United"
    queries += [f"{player_name} footballer", f"{player_name} soccer"]
    for query in queries:
        try:
            data = _wikimedia_get(session, {
                "action": "query", "generator": "search",
                "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": 15,
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiextmetadatafilter": "Artist|LicenseShortName",
                "format": "json",
            })
        except Exception as e:
            print(f"[image_fetcher] player 検索失敗 '{query}': {e}")
            continue

        candidates = []
        for page in data.get("query", {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("mime") not in ("image/jpeg", "image/png"):
                continue
            w, h = info.get("width", 0), info.get("height", 0)
            short = min(w, h)
            if short < 400:
                continue
            # 横長すぎるもの（パノラマ等）は除外
            if h > 0 and w / h > 2.5:
                continue
            candidates.append((short, info, page))

        if not candidates:
            continue

        # 上位5件をシャッフルして順に試す（サイズ上限を超えた場合は次の候補へ）
        candidates.sort(key=lambda x: x[0], reverse=True)
        top = candidates[:5]
        random.shuffle(top)
        for _, info, page in top:
            content = _download(session, info["url"])
            if not content:
                continue
            if len(content) > MAX_IMAGE_BYTES:
                print(f"[image_fetcher] スキップ（{len(content)//1024}KB 超過）: {page.get('title', '')}")
                continue
            filename = _make_filename(page.get("title", player_name))
            attribution = _get_attribution(info.get("extmetadata", {}))
            print(f"[image_fetcher] 選手写真: {player_name} → {filename} ({len(content)//1024}KB)")
            return content, filename, attribution

    return None


def fetch_player_images(
    topic: str,
    max_images: int = 2,
    config_path: str = "config.yaml",
) -> list[tuple[bytes, str, str, str]]:
    """
    記事本文に挿入する選手写真を取得する。
    illustration.enabled=true の場合は取得後にイラスト変換を行う。

    Returns list of (image_bytes, filename, attribution, player_name).
    """
    session = requests.Session()
    session.verify = _SSL_VERIFY

    illust = _is_illustration_enabled(config_path)

    # チーム名を抽出（選手写真クエリの精度向上のため）
    tl = topic.lower()
    team = next((v for k, v in _TEAM_DISPLAY_NAMES.items() if k in tl), "")

    player_names = _extract_all_player_names(topic)

    results: list[tuple[bytes, str, str, str]] = []
    tried: set[str] = set()

    for name in player_names:
        if len(results) >= max_images:
            break
        key = name.lower()
        if key in tried:
            continue
        tried.add(key)

        img = _search_wikimedia_player(session, name, team=team)
        if img:
            content, filename, attribution = img
            if illust:
                from image_converter import convert_to_illustration
                converted = convert_to_illustration(content, filename, player_name=name)
                if converted:
                    content, filename = converted
                    attribution = "Generated with Flux (Replicate)"
            results.append((content, filename, attribution, name))

    return results


# -----------------------------------------------------------------------
# CLI テスト
# -----------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    topic = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Bruno Fernandes Marcus Rashford Manchester United"
    print(f"=== アイキャッチ ===")
    r = fetch_image(topic)
    if r:
        _, name, attr = r
        print(f"  {name} / {attr}")

    print(f"\n=== 選手写真 ===")
    players = fetch_player_images(topic, max_images=3)
    for _, name, attr, player in players:
        print(f"  {player}: {name} / {attr}")
