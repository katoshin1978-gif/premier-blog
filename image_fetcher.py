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
THESPORTSDB_API = "https://www.thesportsdb.com/api/v1/json"
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB 超はWPアップロードでタイムアウトしやすい
DB_PATH = "processed.db"

# 選手写真でサッカーと無関係な場面（訪問・授賞式・私服等）をブロックするキーワード
_BLOCKED_PLAYER_TITLE_KEYWORDS = {
    "visit", "visits", "visited", "visiting",
    "award", "awards", "ceremony",
    "charity", "foundation", "hospital",
    "event", "premiere", "photoshoot",
    "studio", "interview", "press conference",
    "fashion", "casual", "street",
    "shoreditch", "london studio",
    "school", "children",
    "dough", "cooking", "baking", "food",
    # メディア・TV出演
    "presenter", "pundit", "anchor", "host", "commentator", "broadcast",
    "bbc", "sky sports", "itv", "talk show", "podcast", "panel",
    # 商業・広告
    "advert", "advertisement", "commercial", "sponsor", "promotion",
    "launch", "unveil", "unveils", "product",
    # 非サッカーイベント
    # 注意: "ball" は部分一致のため "football"/"footballer" 自体に誤ヒットして
    # サッカー選手の正常な写真まで全てブロックしてしまう。"gala ball" のように
    # 具体的なフレーズのみを対象にする
    "red carpet", "gala", "gala ball", "wedding", "party", "concert",
    "book", "signing", "autobiography",
}

# 汎用すぎてどの記事にも使われてしまうファイルタイトルのキーワード
_BLOCKED_TITLE_KEYWORDS = {
    "fence", "through fence", "watching through", "spectators fence",
    "fans fence", "children fence", "boys fence", "kids watching",
    "fans watching", "supporters watching", "crowd fence",
    "through the fence", "outside stadium", "outside ground",
    "sierra leone", "covid-19 ban", "football devotees", "northern sierra",
    "watch premier league games", "climbed stirs",
    # ロゴ・紋章・ユニフォーム系（選手クエリでもチームロゴがヒットするのを防ぐ）
    "logo", "crest", "badge", "emblem", "seal", "coat of arms",
    "pennant", "flag", "kit", "jersey", "shirt", "strip",
    "icon", "symbol", "wordmark", "monogram",
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

# アイキャッチ検索クエリから「意味のある」キーワードを抽出する際に無視する一般語
# （これらしか残らない場合は関連度チェックを行わず全候補を許可する＝汎用フォールバック用クエリ）
_GENERIC_QUERY_STOPWORDS = {
    "football", "soccer", "match", "matches", "action", "game", "games",
    "premier", "league", "the", "and", "photo", "photograph", "picture",
}

# 選手写真タイトルのサッカー関連度スコアリング用キーワード
# タイトルにこれらが含まれるほど優先度が上がる
_FOOTBALL_TITLE_KEYWORDS = {
    "football", "footballer", "soccer",
    "premier league", "bundesliga", "serie a", "la liga", "ligue 1",
    "champions league", "europa league", "fa cup", "efl",
    " afc", " fc", " utd", " united", " city",
    " vs ", " v ", "match", "training", "pre-season", "preseason",
    "goal", "kick", "tackle", "dribble",
}

# 同名の有名人との混同を防ぐ選手名クエリ上書き（lower()キー → 検索クエリ）
# スコアリングで解決できない特殊ケース用のエスケープハッチ
_PLAYER_QUERY_OVERRIDES: dict[str, str] = {
    "alex scott": "Alex Scott AFC Bournemouth midfielder footballer",
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
    # W杯・大会関連（"Every World Cup Opener Rated" のような見出しで誤抽出される）
    "world", "every", "opener", "rated", "group", "stage", "round", "knock",
    "fixture", "schedule", "format", "venue", "ticket", "tickets", "host",
    "squad", "player", "players", "team", "teams", "nation", "nations",
    "qualify", "qualifier", "qualifiers", "tournament", "competition",
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
    # 見出し疑問文冒頭に来る助動詞（"Can Marcus Rashford be..." のように
    # 隣接する固有名詞と誤ってペア抽出されてしまうのを防ぐ）
    "can", "should", "does", "did", "might", "must",
    "dressing", "room", "after", "about", "against", "around",
    "beyond", "because", "between", "though", "through", "towards",
    # 見出し・箇条書きに出やすい一般語（背番号一覧等のスクワッドページで誤抽出しやすい）
    "detailed", "current", "complete", "full", "list", "listed", "ranking",
    "rankings", "record", "records", "history", "historic", "historical",
    "number", "numbers", "shirt", "kit", "official", "confirmed",
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


def _extract_query_keywords(query: str) -> list[str]:
    """
    検索クエリから関連度チェックに使う意味のある単語だけを抽出する。
    'football'/'match'/'premier'/'league' 等の一般語しか残らない場合は
    空リストを返す（＝汎用フォールバッククエリとして関連度チェックをスキップする合図）。
    """
    words = re.findall(r"[A-Za-z]+", query)
    return [w.lower() for w in words if len(w) >= 3 and w.lower() not in _GENERIC_QUERY_STOPWORDS]


def _football_score(title: str, player_name: str = "", team: str = "") -> int:
    """
    Wikimediaファイルタイトルのサッカー関連度を返す。
    同名の有名人（女性選手・タレント等）より実際のサッカー選手写真を優先するため使用。
    スコアが高いほど優先的に試す。
    """
    tl = title.lower().replace("_", " ")
    score = 0
    if player_name and player_name.lower() in tl:
        score += 5
    if team and team.lower() in tl:
        score += 10  # チーム名一致は最強シグナル
    for kw in _FOOTBALL_TITLE_KEYWORDS:
        if kw in tl:
            score += 3
    return score


# -----------------------------------------------------------------------
# 内部ユーティリティ
# -----------------------------------------------------------------------

def _extract_all_player_names(text: str) -> list[str]:
    """
    テキスト中でテキスト上「実際に隣接している」Title Case単語ペア（名 + 姓）を抽出する。
    同じ人物が重複しないよう lower() でユニーク化する。

    注意: 除外ワードを先にリストから取り除いてから残りを2個ずつペア化する実装だと、
    間に除外ワード（"Squad" 等）を挟んで本来隣接していない単語同士
    （例: "Detailed" + "Diogo"）を誤ってペアにしてしまう。そのため必ず元のテキスト上で
    実際に隣接している単語同士のみをペア候補にする。
    """
    text = re.sub(r"^\[.*?\]\s*", "", text)
    # ダブルクォート（引用発言）のみ除去。シングルクォート/アポストロフィは
    # "Everton's" のような所有格や O'Neil 等の姓に使われるため対象外にする
    # （対象にすると開始/終了記号を誤認して間の姓名を丸ごと消してしまう）
    text = re.sub(r'["""][^"""]*["""]', "", text)

    pairs: list[str] = []
    seen: set[str] = set()

    # O'Neil / D'Ambrosio 等アポストロフィ付き姓名を優先抽出
    # finditer でマッチしながら書き換えると位置がずれるため、先に全マッチを収集してから逆順で置換
    apostrophe_matches = list(re.finditer(
        r"\b([A-Z][a-z]{2,})\s+([A-Z][a-z]?['''][A-Z][a-z]{2,})\b", text
    ))
    for m in apostrophe_matches:
        first, last = m.group(1), m.group(2)
        if first.lower() in _EXCLUDE_WORDS:
            continue
        full = f"{first} {last}"
        key = full.lower()
        if key not in seen:
            seen.add(key)
            pairs.append(full)
    # 抽出済みの部分を空白で潰して通常抽出で再ヒットしないようにする（逆順で位置ずれ回避）
    for m in reversed(apostrophe_matches):
        text = text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]

    # テキスト上で直接隣接している大文字始まり単語ペアのみを候補にする（finditerは非重複走査）
    for m in re.finditer(r"\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b", text):
        w1, w2 = m.group(1), m.group(2)
        if w1.lower() in _EXCLUDE_WORDS or w2.lower() in _EXCLUDE_WORDS:
            continue
        # 名前ペア判定: 両語とも4文字以上 or 片方が3文字で他方が5文字以上（Yan, Ben等の短い名前に対応）
        if not ((len(w1) >= 4 and len(w2) >= 4) or (len(w1) == 3 and len(w2) >= 5)):
            continue
        pair = f"{w1} {w2}"
        key = pair.lower()
        if key not in seen:
            seen.add(key)
            pairs.append(pair)

    # ペアになれなかった単独の5文字以上の単語（既にペアで使われた単語は除く）
    used_words = {w.lower() for p in pairs for w in p.split()}
    for w in re.findall(r"\b[A-Z][a-z]{4,}\b", text):
        if w.lower() in _EXCLUDE_WORDS or w.lower() in used_words:
            continue
        key = w.lower()
        if key not in seen:
            seen.add(key)
            pairs.append(w)

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
# TheSportsDB
# -----------------------------------------------------------------------

def _thesportsdb_key() -> str:
    return os.environ.get("THESPORTSDB_API_KEY", "3")


def _search_thesportsdb_player(
    session: requests.Session, player_name: str
) -> tuple[bytes, str, str] | None:
    """TheSportsDB で選手写真（strThumb）を取得する"""
    try:
        resp = session.get(
            f"{THESPORTSDB_API}/{_thesportsdb_key()}/searchplayers.php",
            params={"p": player_name},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        resp.raise_for_status()
        players = resp.json().get("player") or []
    except Exception as e:
        print(f"[image_fetcher] TheSportsDB player 検索失敗 '{player_name}': {e}")
        return None

    _get_db()
    for player in players[:5]:
        for field in ("strThumb", "strCutout", "strRender"):
            url = player.get(field)
            if not url or not url.startswith("http"):
                continue
            ext = url.split(".")[-1].split("?")[0].lower()
            if ext not in ("jpg", "jpeg", "png"):
                continue
            filename = f"sportsdb_{player.get('idPlayer', 'p')}_{field.lower()}.jpg"
            if _is_image_used(filename):
                continue
            content = _download(session, url)
            if not content or len(content) > MAX_IMAGE_BYTES:
                continue
            _mark_image_used(filename)
            print(f"[image_fetcher] TheSportsDB 選手写真: {player_name} → {filename} ({len(content)//1024}KB) 元URL={url}")
            return content, filename, f"TheSportsDB / {player.get('strPlayer', player_name)}"
    return None


def _search_thesportsdb_team(
    session: requests.Session, team_name: str
) -> tuple[bytes, str, str] | None:
    """TheSportsDB でチームのファンアート（横長）を取得する"""
    try:
        resp = session.get(
            f"{THESPORTSDB_API}/{_thesportsdb_key()}/searchteams.php",
            params={"t": team_name},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        resp.raise_for_status()
        teams = resp.json().get("teams") or []
    except Exception as e:
        print(f"[image_fetcher] TheSportsDB team 検索失敗 '{team_name}': {e}")
        return None

    if not teams:
        return None

    team = teams[0]
    _get_db()
    fanart_fields = ["strTeamFanart1", "strTeamFanart2", "strTeamFanart3", "strTeamFanart4", "strTeamFanart5", "strTeamBanner"]
    urls = [team.get(f) for f in fanart_fields if team.get(f)]
    random.shuffle(urls)
    for url in urls:
        if not url or not url.startswith("http"):
            continue
        team_slug = re.sub(r"\W", "_", team_name.lower())
        filename = f"sportsdb_team_{team_slug}.jpg"
        if _is_image_used(filename):
            filename = f"sportsdb_team_{team_slug}_{random.randint(1000,9999)}.jpg"
        content = _download(session, url)
        if not content or len(content) > MAX_IMAGE_BYTES:
            continue
        _mark_image_used(filename)
        print(f"[image_fetcher] TheSportsDB チーム画像: {team_name} → {filename} ({len(content)//1024}KB)")
        return content, filename, f"TheSportsDB / {team.get('strTeam', team_name)}"
    return None


def _search_thesportsdb_league(
    session: requests.Session, league_id: int = 4328
) -> tuple[bytes, str, str] | None:
    """TheSportsDB でリーグのファンアート/バナーを取得する（デフォルト: Premier League ID=4328）"""
    try:
        resp = session.get(
            f"{THESPORTSDB_API}/{_thesportsdb_key()}/lookupleague.php",
            params={"id": league_id},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        resp.raise_for_status()
        leagues = resp.json().get("leagues") or []
    except Exception as e:
        print(f"[image_fetcher] TheSportsDB league 検索失敗 (id={league_id}): {e}")
        return None

    if not leagues:
        return None

    league = leagues[0]
    _get_db()
    fanart_fields = ["strFanart1", "strFanart2", "strFanart3", "strFanart4", "strBanner"]
    urls = [league.get(f) for f in fanart_fields if league.get(f)]
    random.shuffle(urls)
    for url in urls:
        if not url or not url.startswith("http"):
            continue
        slug = re.sub(r"[^\w]", "_", league.get("strLeague", "league").lower())
        filename = f"sportsdb_league_{slug}_{random.randint(1000, 9999)}.jpg"
        if _is_image_used(filename):
            continue
        content = _download(session, url)
        if not content or len(content) > MAX_IMAGE_BYTES:
            continue
        _mark_image_used(filename)
        print(f"[image_fetcher] TheSportsDB リーグ画像: {league.get('strLeague', '')} → {filename} ({len(content)//1024}KB)")
        return content, filename, f"TheSportsDB / {league.get('strLeague', 'Premier League')}"
    return None


# -----------------------------------------------------------------------
# アイキャッチ画像（横長・試合写真）
# -----------------------------------------------------------------------

def _search_wikimedia_landscape(
    session: requests.Session, query: str, require_relevance: bool = True
) -> tuple[bytes, str, str] | None:
    """
    アイキャッチ用写真を取得する（縦長もOK、pad_to_landscapeで横長化する）。

    require_relevance=True の場合、クエリからチーム名・選手名などの意味のある
    キーワードを抽出し、タイトルにそのキーワードが実際に含まれる候補のみを許可する。
    Wikimedia の gsrsearch はあいまい一致（説明文・カテゴリにも一致）のため、
    これがないとタイトルに無関係な高解像度画像が選ばれてしまうことがある。
    """
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
        if min(w, h) < 400:
            continue
        candidates.append((w, info, page))

    if not candidates:
        return None

    keywords = _extract_query_keywords(query) if require_relevance else []
    if keywords:
        relevant = [
            c for c in candidates
            if any(kw in c[2].get("title", "").lower().replace("_", " ") for kw in keywords)
        ]
        if not relevant:
            print(f"[image_fetcher] 関連画像なし（キーワード不一致）: query='{query}' 候補{len(candidates)}件中0件が一致")
            return None
        candidates = relevant

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
        print(f"[image_fetcher] アイキャッチ: {filename} ({len(content)//1024}KB) query='{query}' 元URL={info['url']}")
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


def _search_tavily_images(
    session: requests.Session, api_key: str, query: str, landscape: bool = True
) -> tuple[bytes, str, str] | None:
    """
    Tavily 画像検索で写真を取得する。
    landscape=True: 横長（アイキャッチ用）、False: 縦長or正方形（選手写真用）
    """
    import io
    from PIL import Image as PilImage
    try:
        resp = session.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "include_images": True, "max_results": 5},
            timeout=15,
        )
        resp.raise_for_status()
        image_urls = resp.json().get("images", [])
    except Exception as e:
        print(f"[image_fetcher] Tavily 画像検索失敗 '{query}': {e}")
        return None

    if not image_urls:
        return None

    random.shuffle(image_urls)
    _get_db()
    for url in image_urls:
        if not url.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        content = _download(session, url)
        if not content or len(content) > MAX_IMAGE_BYTES:
            continue
        try:
            img = PilImage.open(io.BytesIO(content))
            w, h = img.size
        except Exception:
            continue
        if landscape:
            if min(w, h) < 400:
                continue
        else:
            short = min(w, h)
            if short < 400 or (h > 0 and w / h > 2.5):
                continue
        filename = re.sub(r"[^\w.-]", "_", url.split("/")[-1].split("?")[0]) or "tavily_image.jpg"
        if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
            filename += ".jpg"
        if _is_image_used(filename):
            continue
        _mark_image_used(filename)
        label = "アイキャッチ" if landscape else "選手写真"
        print(f"[image_fetcher] Tavily {label}: {filename} ({len(content)//1024}KB) query='{query}' 元URL={url}")
        return content, filename, "Tavily Search"
    return None


def _is_illustration_enabled(config_path: str = "config.yaml") -> bool:
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return bool(cfg.get("illustration", {}).get("enabled", False))
    except Exception:
        return False


def fetch_image(topic: str, config_path: str = "config.yaml", primary_topic: str = "") -> tuple[bytes, str, str] | None:
    """
    アイキャッチ用の横長画像を取得する。
    illustration.enabled=true の場合は Flux でイラスト生成。
    それ以外は Wikimedia Commons → Pexels の順でフォールバック。

    primary_topic: 記事本来の主題（生成された記事タイトル等）。指定されていれば
    ソース記事タイトルを結合したtopic全体より優先して選手名抽出を行う。
    topicにはソース記事タイトルなど周辺情報が混ざっており、そちらだけから抽出すると
    無関係な選手名を誤って拾うことがあるため。
    Returns (image_bytes, filename, attribution) or None.
    """
    session = requests.Session()
    session.verify = _SSL_VERIFY

    tl = topic.lower()
    team_query = next((q for kw, q in _TEAM_QUERIES.items() if kw in tl), None)

    # 記事本来の主題を優先して選手名抽出。見つからなければ結合テキスト全体にフォールバック
    player = None
    if primary_topic:
        primary_players = _extract_all_player_names(primary_topic)
        player = next((p for p in primary_players if " " in p), None)
    if not player:
        players = _extract_all_player_names(topic)
        # 2語（姓名）のペアのみアイキャッチクエリに使用（単語だけでは誤検出しやすいため）
        player = next((p for p in players if " " in p), None)

    queries = []
    if player:
        # "footballer" を付けると人物写真に絞られロゴ画像を避けられる
        queries.append(f"{player} footballer")
        queries.append(f"{player} football match")
    if team_query:
        queries.append(team_query)
    queries.append("Premier League football match action")

    tavily_key = os.environ.get("TAVILY_API_KEY", "")

    def _pad_and_return(res: tuple[bytes, str, str]) -> tuple[bytes, str, str]:
        from image_converter import pad_to_landscape
        content, filename, attribution = res
        content = pad_to_landscape(content)
        return content, filename, attribution

    # 写真取得（TheSportsDB → Tavily → Wikimedia → Pexels → 汎用Wikimedia）
    photo_result = None
    # TheSportsDB: 選手名があれば選手サムネイルを最初に試す（無料APIで取得可能）
    if player:
        photo_result = _search_thesportsdb_player(session, player)
    # TheSportsDB: チーム名があればファンアートも試す（有料APIで追加取得可能）
    if not photo_result and team_query:
        team_display = next((v for k, v in _TEAM_DISPLAY_NAMES.items() if k in tl), None)
        if team_display:
            photo_result = _search_thesportsdb_team(session, team_display)
    # TheSportsDB: 選手もチームも特定できない汎用トピック → リーグ画像を優先使用
    if not photo_result and not player and not team_query:
        photo_result = _search_thesportsdb_league(session)
    if not photo_result and tavily_key:
        for query in queries:
            photo_result = _search_tavily_images(session, tavily_key, query, landscape=True)
            if photo_result:
                break
    if not photo_result:
        # 選手名がある場合は縦長個人写真を先に試みる（幅ソートの横長検索より精度が高い）
        if player:
            player_photo = _search_wikimedia_player(session, player)
            if player_photo:
                photo_result = player_photo
                print(f"[image_fetcher] Wikimedia選手写真をアイキャッチに使用: {player}")
    if not photo_result:
        for query in queries:
            photo_result = _search_wikimedia_landscape(session, query)
            if photo_result:
                break
    if not photo_result:
        pexels_key = os.environ.get("PEXELS_API_KEY", "")
        if pexels_key:
            pexels_queries = queries + ["soccer football stadium", "football match crowd"]
            for pq in pexels_queries:
                photo_result = _search_pexels(session, pexels_key, pq)
                if photo_result:
                    break
    if not photo_result:
        for fallback_q in ["Association football", "football stadium", "soccer match"]:
            # 最終フォールバックは意図的に汎用クエリのため関連度チェックはスキップ
            photo_result = _search_wikimedia_landscape(session, fallback_q, require_relevance=False)
            if photo_result:
                print(f"[image_fetcher] 最終フォールバック画像使用: {fallback_q}")
                break

    if photo_result:
        # illustration modeならFlux変換を試みる（失敗したらpad済み写真をそのまま使う）
        if _is_illustration_enabled(config_path):
            from image_converter import convert_to_realistic_featured
            padded_bytes, _, _ = _pad_and_return(photo_result)
            converted = convert_to_realistic_featured(padded_bytes, photo_result[1], topic)
            if converted:
                art_bytes, art_filename = converted
                return art_bytes, art_filename, "Generated with Flux (Replicate)"
            print("[image_fetcher] Flux変換失敗 → pad写真をそのまま使用")
        return _pad_and_return(photo_result)

    # 写真が全滅した場合のみロゴ生成
    if _is_illustration_enabled(config_path):
        from image_converter import generate_logo_image
        logo = generate_logo_image(topic)
        if logo:
            print("[image_fetcher] 写真取得不可 → ロゴ生成")
            return logo

    return None


# -----------------------------------------------------------------------
# 記事内選手画像（縦長・個人写真）
# -----------------------------------------------------------------------

def _search_wikimedia_player(
    session: requests.Session, player_name: str, team: str = ""
) -> tuple[bytes, str, str] | None:
    """選手個人の写真（縦長 or ほぼ正方形、400px以上）を取得する"""
    override = _PLAYER_QUERY_OVERRIDES.get(player_name.lower())
    if override:
        queries = [override]
    else:
        queries = []
        if team:
            queries.append(f"{player_name} {team} football")  # チーム名+footballで試合写真に絞る
            queries.append(f"{player_name} {team}")
        queries += [f"{player_name} footballer", f"{player_name} football match"]
    for query in queries:
        try:
            data = _wikimedia_get(session, {
                "action": "query", "generator": "search",
                "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": 20,
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
            title = page.get("title", "")
            tl = title.lower().replace("_", " ")
            if any(kw in tl for kw in _BLOCKED_PLAYER_TITLE_KEYWORDS):
                print(f"[image_fetcher] 選手写真スキップ（非サッカー）: {title}")
                continue
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
            fscore = _football_score(title, player_name, team)
            candidates.append((fscore, short, info, page))

        if not candidates:
            continue

        # スコア0（タイトルに選手名・チーム名・サッカー関連語が一切含まれない）候補は
        # 同名の無関係な人物・被写体である可能性が高いため除外し、次のクエリ変種に委ねる
        positive = [c for c in candidates if c[0] > 0]
        if not positive:
            print(f"[image_fetcher] 選手写真候補すべて関連度0（無関係の可能性）: query='{query}'")
            continue
        candidates = positive

        # サッカー関連スコア降順→解像度降順でソートして上位5件を試す
        # スコアが高い＝タイトルにサッカー・チーム関連語が含まれる＝同名の非サッカー人物より優先
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        top = candidates[:5]
        random.shuffle(top[:3])  # 同スコア帯内はランダム性を残す
        _get_db()
        for fscore, _, info, page in top:
            title = page.get("title", player_name)
            filename = _make_filename(title)
            if _is_image_used(filename):
                print(f"[image_fetcher] 選手写真スキップ（使用済み）: {filename}")
                continue
            content = _download(session, info["url"])
            if not content:
                continue
            if len(content) > MAX_IMAGE_BYTES:
                print(f"[image_fetcher] スキップ（{len(content)//1024}KB 超過）: {title}")
                continue
            attribution = _get_attribution(info.get("extmetadata", {}))
            _mark_image_used(filename)
            print(f"[image_fetcher] 選手写真: {player_name} → {filename} score={fscore} ({len(content)//1024}KB) 元URL={info['url']}")
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

        # TheSportsDB優先 → Wikimedia → Tavily
        img = _search_thesportsdb_player(session, name)
        if not img:
            img = _search_wikimedia_player(session, name, team=team)
        if not img:
            tavily_key = os.environ.get("TAVILY_API_KEY", "")
            if tavily_key:
                override = _PLAYER_QUERY_OVERRIDES.get(name.lower())
                if override:
                    queries = [override]
                else:
                    queries = []
                    if team:
                        queries.append(f"{name} {team} footballer")
                    queries += [f"{name} footballer", f"{name} football match"]
                for q in queries:
                    img = _search_tavily_images(session, tavily_key, q, landscape=False)
                    if img:
                        break
        if img:
            content, filename, attribution = img
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
