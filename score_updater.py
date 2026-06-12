"""
スコアティッカー自動更新モジュール
football-data.org API から PL 最新スコアを取得し、
WordPress ACF オプション（ticker_label / ticker_scores）を更新する
"""

import base64
import os
from datetime import datetime, timedelta, timezone

import requests
import yaml
from dotenv import load_dotenv

load_dotenv()

_SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() != "false"
FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
PL_ID = "PL"
JST = timezone(timedelta(hours=9))

# 4大リーグ定義
MULTI_LEAGUES = [
    {"id": "PL",  "name": "プレミアリーグ"},
    {"id": "PD",  "name": "ラ・リーガ"},
    {"id": "BL1", "name": "ブンデスリーガ"},
    {"id": "SA",  "name": "セリエA"},
]


def _fd_headers() -> dict:
    return {"X-Auth-Token": os.environ.get("FOOTBALL_DATA_API_KEY", "")}


def _wp_auth_header() -> dict:
    token = base64.b64encode(
        f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()
    ).decode()
    return {"Authorization": f"Basic {token}"}


def _team_name(team: dict) -> str:
    return team.get("shortName") or team.get("tla") or team.get("name", "?")


def _format_match(match: dict) -> dict:
    status = match.get("status", "")
    ft = match.get("score", {}).get("fullTime", {})
    h, a = ft.get("home"), ft.get("away")

    if status == "FINISHED":
        score_str = f"{h} - {a}" if h is not None else "? - ?"
        return {"score": score_str, "time": "FT", "is_live": False}

    if status == "IN_PLAY":
        score_str = f"{h} - {a}" if h is not None else "0 - 0"
        minute = match.get("minute") or ""
        return {"score": score_str, "time": f"{minute}'" if minute else "●", "is_live": True}

    if status == "PAUSED":
        score_str = f"{h} - {a}" if h is not None else "0 - 0"
        return {"score": score_str, "time": "HT", "is_live": True}

    # SCHEDULED など
    utc_date = match.get("utcDate", "")
    time_str = "--:--"
    if utc_date:
        try:
            dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
            time_str = dt.astimezone(JST).strftime("%H:%M")
        except Exception:
            pass
    return {"score": "v", "time": time_str, "is_live": False}


def _fetch_latest_pl_matches() -> list[dict]:
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    if not api_key or api_key == "your_football_data_api_key_here":
        print("[score_updater] FOOTBALL_DATA_API_KEY 未設定のためスキップ")
        return []

    headers = _fd_headers()
    today = datetime.now(JST).date()

    # ① ライブ試合を優先確認
    try:
        r = requests.get(
            f"{FOOTBALL_DATA_BASE}/competitions/{PL_ID}/matches",
            headers=headers,
            params={"status": "IN_PLAY,PAUSED"},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        r.raise_for_status()
        live = r.json().get("matches", [])
        if live:
            return live
    except Exception as e:
        print(f"[score_updater] ライブ試合取得失敗: {e}")

    # ② 直近 14 日間の結果
    try:
        date_from = (today - timedelta(days=14)).isoformat()
        date_to = today.isoformat()
        r = requests.get(
            f"{FOOTBALL_DATA_BASE}/competitions/{PL_ID}/matches",
            headers=headers,
            params={"dateFrom": date_from, "dateTo": date_to},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        r.raise_for_status()
        recent = r.json().get("matches", [])
        if recent:
            latest_day = max(m["matchday"] for m in recent)
            return [m for m in recent if m["matchday"] == latest_day]
    except Exception as e:
        print(f"[score_updater] 直近試合取得失敗: {e}")

    # ③ シーズンオフ対応: 最終節の結果を取得
    try:
        r = requests.get(
            f"{FOOTBALL_DATA_BASE}/competitions/{PL_ID}/matches",
            headers=headers,
            params={"status": "FINISHED"},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        r.raise_for_status()
        finished = r.json().get("matches", [])
        if finished:
            latest_day = max(m["matchday"] for m in finished)
            return [m for m in finished if m["matchday"] == latest_day]
    except Exception as e:
        print(f"[score_updater] 完了試合取得失敗: {e}")

    return []


def _update_acf_options(label: str, scores: list[dict], wp_url: str) -> bool:
    endpoint = f"{wp_url.rstrip('/')}/wp-json/premier-blog/v1/ticker"
    headers = {**_wp_auth_header(), "Content-Type": "application/json"}
    try:
        r = requests.post(
            endpoint,
            json={"ticker_label": label, "ticker_scores": scores},
            headers=headers,
            timeout=15,
            verify=_SSL_VERIFY,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[score_updater] ACF 更新失敗: {e}")
        return False


_MAN_UNITED_NAMES = {"manchester united", "man united", "man utd", "manchester utd"}

# リーグ別の降格開始順位（その順位以下が降格圏）
_LEAGUE_RELEGATION_START = {
    "PL":  18,  # 20チーム: 18〜20位
    "PD":  18,
    "BL1": 16,  # 18チーム: 16〜18位
    "SA":  18,
}
_CL_SPOTS = 4  # 上位4クラブがCL


def _fetch_scorers(league_id: str, limit: int = 50) -> list[dict]:
    """指定リーグの得点ランキングを取得"""
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    if not api_key or api_key == "your_football_data_api_key_here":
        return []
    try:
        r = requests.get(
            f"{FOOTBALL_DATA_BASE}/competitions/{league_id}/scorers",
            headers=_fd_headers(),
            params={"limit": limit},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        r.raise_for_status()
        return r.json().get("scorers", [])
    except Exception as e:
        print(f"[score_updater] {league_id} 得点ランキング取得失敗: {e}")
        return []


def _fetch_top_scorers() -> list[dict]:
    return _fetch_scorers(PL_ID)


def _fetch_next_fixtures() -> list[dict]:
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    if not api_key or api_key == "your_football_data_api_key_here":
        return []
    try:
        r = requests.get(
            f"{FOOTBALL_DATA_BASE}/competitions/{PL_ID}/matches",
            headers=_fd_headers(),
            params={"status": "SCHEDULED"},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        r.raise_for_status()
        matches = r.json().get("matches", [])
        if not matches:
            return []
        next_matchday = min(m["matchday"] for m in matches)
        return [m for m in matches if m["matchday"] == next_matchday]
    except Exception as e:
        print(f"[score_updater] 次節日程取得失敗: {e}")
        return []


def _update_acf_general(fields: dict, wp_url: str) -> bool:
    """汎用ACFオプション更新エンドポイントを呼び出す"""
    endpoint = f"{wp_url.rstrip('/')}/wp-json/premier-blog/v1/acf-options"
    headers = {**_wp_auth_header(), "Content-Type": "application/json"}
    try:
        r = requests.post(endpoint, json={"fields": fields}, headers=headers, timeout=15, verify=_SSL_VERIFY)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[score_updater] ACFオプション更新失敗: {e}")
        return False


def _update_fixtures(matches: list[dict], wp_url: str, is_results: bool = False) -> bool:
    if not matches:
        return False
    matchday = matches[0].get("matchday", "?")
    fixtures = []
    for m in matches:
        utc_date = m.get("utcDate", "")
        day_str, kickoff_str = "--", "--:--"
        if utc_date:
            try:
                dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
                jst = dt.astimezone(JST)
                day_str = f"{jst.month}/{jst.day}"
                kickoff_str = jst.strftime("%H:%M")
            except Exception:
                pass
        fmt = _format_match(m)
        fixtures.append({
            "day": day_str,
            "kickoff": kickoff_str,
            "home": _team_name(m.get("homeTeam", {})),
            "away": _team_name(m.get("awayTeam", {})),
            "score": fmt["score"] if is_results else "",
            "time": fmt["time"] if is_results else "",
            "is_live": fmt["is_live"],
        })

    label = f"第{matchday}節 結果" if is_results else f"第{matchday}節"
    ok = _update_acf_general({
        "fixtures_section_label": label,
        "fixtures": fixtures,
    }, wp_url)
    if ok:
        print(f"[score_updater] {'直近結果' if is_results else '次節日程'}更新完了: {label} ({len(fixtures)}試合)")
    return ok


def _update_data_board(scorers: list[dict], matches: list[dict], standings_data: dict, wp_url: str) -> bool:
    board = []

    # 得点王
    if scorers:
        top = scorers[0]
        name = top.get("player", {}).get("name", "?").split()[-1]  # 苗字のみ
        goals = top.get("goals", 0)
        board.append({"value": f"{goals}G", "label": f"{name}（得点王）", "delta": "", "is_negative": False})

    # アシスト王（assistsで並べ直す）
    if scorers:
        top_assist = max(scorers, key=lambda s: s.get("assists") or 0)
        name = top_assist.get("player", {}).get("name", "?").split()[-1]
        assists = top_assist.get("assists") or 0
        board.append({"value": f"{assists}A", "label": f"{name}（アシスト王）", "delta": "", "is_negative": False})

    # 今節総得点
    matchday_goals = 0
    for m in matches:
        if m.get("status") == "FINISHED":
            ft = m.get("score", {}).get("fullTime", {})
            matchday_goals += (ft.get("home") or 0) + (ft.get("away") or 0)
    if matchday_goals > 0:
        board.append({"value": str(matchday_goals), "label": "今節総得点", "delta": "", "is_negative": False})

    # シーズン総得点（standings から算出）
    standings = standings_data.get("standings", [])
    total_table = next((s for s in standings if s.get("type") == "TOTAL"), None)
    if total_table:
        season_goals = sum(e.get("goalsFor", 0) for e in total_table.get("table", []))
        board.append({"value": str(season_goals), "label": "シーズン総得点", "delta": "", "is_negative": False})

    if not board:
        return False

    ok = _update_acf_general({"data_board": board[:4]}, wp_url)
    if ok:
        print(f"[score_updater] データボード更新完了 ({len(board)}項目)")
    return ok


def _fetch_standings(league_id: str) -> dict:
    """指定リーグの順位表を取得"""
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    if not api_key or api_key == "your_football_data_api_key_here":
        return {}
    try:
        r = requests.get(
            f"{FOOTBALL_DATA_BASE}/competitions/{league_id}/standings",
            headers=_fd_headers(),
            timeout=15,
            verify=_SSL_VERIFY,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[score_updater] {league_id} 順位表取得失敗: {e}")
        return {}


def _fetch_pl_standings() -> dict:
    return _fetch_standings(PL_ID)


def _build_league_payload(league_id: str, league_name: str, standings_data: dict, scorers: list[dict]) -> dict:
    """1リーグ分のペイロードを構築（順位表・得失点・得点王/アシスト王）"""
    standings = standings_data.get("standings", [])
    total = next((s for s in standings if s.get("type") == "TOTAL"), None)
    matchday = standings_data.get("season", {}).get("currentMatchday", "?")

    rel_start = _LEAGUE_RELEGATION_START.get(league_id, 18)

    table = []
    if total:
        for entry in total.get("table", []):
            pos = entry.get("position", 0)
            team = entry.get("team", {})
            name = team.get("shortName") or team.get("name", "?")
            gd = entry.get("goalDifference", 0)
            table.append({
                "position": pos,
                "club": name,
                "played": entry.get("playedGames", 0),
                "w": entry.get("won", 0),
                "d": entry.get("draw", 0),
                "l": entry.get("lost", 0),
                "gf": entry.get("goalsFor", 0),
                "ga": entry.get("goalsAgainst", 0),
                "gd": f"+{gd}" if gd > 0 else str(gd),
                "pts": entry.get("points", 0),
                "is_cl": pos <= _CL_SPOTS,
                "is_relegated": pos >= rel_start,
                "is_highlight": name.lower() in _MAN_UNITED_NAMES,
            })

    top_scorer: dict = {}
    top_assister: dict = {}
    if scorers:
        s = scorers[0]
        top_scorer = {
            "name": s.get("player", {}).get("name", "?"),
            "club": (s.get("team") or {}).get("shortName") or (s.get("team") or {}).get("name", "?"),
            "goals": s.get("goals", 0),
        }
        a = max(scorers, key=lambda x: x.get("assists") or 0)
        top_assister = {
            "name": a.get("player", {}).get("name", "?"),
            "club": (a.get("team") or {}).get("shortName") or (a.get("team") or {}).get("name", "?"),
            "assists": a.get("assists") or 0,
        }

    return {
        "id": league_id,
        "name": league_name,
        "matchday": f"第{matchday}節",
        "table": table,
        "top_scorer": top_scorer,
        "top_assister": top_assister,
    }


def _update_multi_league(wp_url: str) -> bool:
    """4大リーグの順位表・得失点・得点王/アシスト王をWordPressに送信"""
    import time
    leagues_payload = []

    for i, league in enumerate(MULTI_LEAGUES):
        if i > 0:
            time.sleep(7)  # レート制限対策（10req/min）
        lid = league["id"]
        lname = league["name"]
        standings_data = _fetch_standings(lid)
        if not standings_data:
            print(f"[score_updater] {lname} データ取得スキップ")
            continue
        time.sleep(7)
        scorers = _fetch_scorers(lid)
        payload = _build_league_payload(lid, lname, standings_data, scorers)
        leagues_payload.append(payload)
        print(f"[score_updater] {lname}: {len(payload['table'])}クラブ取得")

    if not leagues_payload:
        return False

    endpoint = f"{wp_url.rstrip('/')}/wp-json/premier-blog/v1/multi-league"
    headers = {**_wp_auth_header(), "Content-Type": "application/json"}
    try:
        r = requests.post(
            endpoint,
            json={"leagues": leagues_payload},
            headers=headers,
            timeout=30,
            verify=_SSL_VERIFY,
        )
        r.raise_for_status()
        print(f"[score_updater] 4大リーグデータ更新完了 ({len(leagues_payload)}リーグ)")
        return True
    except Exception as e:
        print(f"[score_updater] 4大リーグデータ更新失敗: {e}")
        return False


def _update_league_table(standings_data: dict, wp_url: str) -> bool:
    standings = standings_data.get("standings", [])
    total = next((s for s in standings if s.get("type") == "TOTAL"), None)
    if not total:
        return False

    matchday = standings_data.get("season", {}).get("currentMatchday", "?")
    matchweek_label = f"第{matchday}節終了"

    rows = []
    for entry in total.get("table", []):
        pos  = entry.get("position", 0)
        team = entry.get("team", {})
        name = team.get("shortName") or team.get("name", "?")
        gd   = entry.get("goalDifference", 0)
        rows.append({
            "position":     pos,
            "club":         name,
            "w":            entry.get("won", 0),
            "d":            entry.get("draw", 0),
            "l":            entry.get("lost", 0),
            "gd":           f"+{gd}" if gd > 0 else str(gd),
            "pts":          entry.get("points", 0),
            "is_cl":        pos <= 4,
            "is_el":        pos == 5,
            "is_relegated": pos >= 18,
            "is_highlight": name.lower() in _MAN_UNITED_NAMES,
        })

    endpoint = f"{wp_url.rstrip('/')}/wp-json/premier-blog/v1/league-table"
    headers  = {**_wp_auth_header(), "Content-Type": "application/json"}
    try:
        r = requests.post(
            endpoint,
            json={"league_table_matchweek": matchweek_label, "league_table": rows},
            headers=headers,
            timeout=15,
            verify=_SSL_VERIFY,
        )
        r.raise_for_status()
        print(f"[score_updater] 順位表更新完了: {matchweek_label} ({len(rows)}クラブ)")
        return True
    except Exception as e:
        print(f"[score_updater] 順位表更新失敗: {e}")
        return False


def update_ticker(config_path: str = "config.yaml") -> bool:
    """PL 最新スコアでティッカーを更新する。失敗しても例外を投げない。"""
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    wp_url = os.environ.get("WP_URL", cfg.get("wordpress", {}).get("url", ""))

    matches = _fetch_latest_pl_matches()

    # ティッカー更新（試合がある場合のみ）
    success = False
    if not matches:
        print("[score_updater] 対象試合なし、ティッカースキップ（順位表・日程・データボードは更新継続）")
    else:
        matchday = matches[0].get("matchday", "?")
        any_live = any(m.get("status") in ("IN_PLAY", "PAUSED") for m in matches)
        all_done = all(m.get("status") == "FINISHED" for m in matches)

        if any_live:
            label = f"第{matchday}節 LIVE"
        elif all_done:
            label = f"第{matchday}節 結果"
        else:
            label = f"第{matchday}節 速報"

        ticker_scores = []
        for m in matches:
            fmt = _format_match(m)
            ticker_scores.append({
                "home": _team_name(m.get("homeTeam", {})),
                "score": fmt["score"],
                "away": _team_name(m.get("awayTeam", {})),
                "time": fmt["time"],
                "is_live": fmt["is_live"],
            })

        print(f"[score_updater] {len(ticker_scores)} 試合取得: {label}")
        success = _update_acf_options(label, ticker_scores, wp_url)
        if success:
            print(f"[score_updater] ティッカー更新完了")

    # 順位表更新（シーズンオフでも最終節データが取得できる）
    standings_data = _fetch_pl_standings()
    if standings_data:
        _update_league_table(standings_data, wp_url)

    # 次節日程更新（シーズンオフは直近完了試合をフォールバック）
    next_fixtures = _fetch_next_fixtures()
    if next_fixtures:
        _update_fixtures(next_fixtures, wp_url)
    elif matches:
        _update_fixtures(matches, wp_url, is_results=True)

    # データボード更新（得点王・アシスト王・今節・シーズン総得点）
    scorers = _fetch_top_scorers()
    _update_data_board(scorers, matches, standings_data, wp_url)

    return success


if __name__ == "__main__":
    update_ticker()
