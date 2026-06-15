"""
WordPress投稿モジュール
WordPress REST API で「下書き(draft)」として投稿
"""

import os
import base64
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import requests
import urllib3
import yaml
from dotenv import load_dotenv

load_dotenv()

# 企業プロキシ環境など SSL 検証が通らない場合は .env で SSL_VERIFY=false を設定
_SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() != "false"
if not _SSL_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _resolve_to_ip(url: str) -> tuple[str, dict]:
    """
    ホスト名をIPに解決してDNSブロック（Cisco Umbrella等）を回避。
    Returns (ip_based_url, {"Host": hostname})
    失敗時は元のURLをそのまま返す。
    """
    parsed = urlparse(url)
    try:
        ip = socket.gethostbyname(parsed.hostname)
        port = f":{parsed.port}" if parsed.port else ""
        new_netloc = f"{ip}{port}"
        ip_url = urlunparse(parsed._replace(netloc=new_netloc))
        return ip_url, {"Host": parsed.hostname}
    except Exception:
        return url, {}


@dataclass
class PublishResult:
    post_id: int
    url: str
    status: str
    title: str


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_auth_header() -> dict:
    username = os.environ["WP_USERNAME"]
    app_password = os.environ["WP_APP_PASSWORD"]
    token = base64.b64encode(f"{username}:{app_password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _strip_header_section(markdown: str) -> str:
    """
    本文冒頭の「# タイトル」と「## はじめに」ブロックを除去する。
    WordPressが投稿タイトルを別途表示するため、記事冒頭のh1は不要。
    「## はじめに」はリード文として冗長なため、その見出しのみ削除する。
    """
    lines = markdown.splitlines()
    result = []
    skip_intro_heading = False
    for line in lines:
        stripped = line.strip()
        # # タイトル行を削除
        if stripped.startswith("# ") and not stripped.startswith("## "):
            continue
        # ## はじめに の見出しのみ削除（本文は残す）
        if stripped in ("## はじめに", "## はじめに"):
            skip_intro_heading = True
            continue
        skip_intro_heading = False
        result.append(line)
    return "\n".join(result)


def markdown_to_html(markdown: str) -> str:
    """シンプルなMarkdown→HTML変換（見出し・リンク・強調）"""
    markdown = _strip_header_section(markdown)
    lines = markdown.splitlines()
    html_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        # 水平線
        if stripped == "---":
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<hr>")
            continue

        # h1
        if stripped.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = stripped[2:]
            text = _inline_format(text)
            html_lines.append(f"<h1>{text}</h1>")
            continue

        # h2
        if stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = stripped[3:]
            text = _inline_format(text)
            html_lines.append(f"<h2>{text}</h2>")
            continue

        # h3
        if stripped.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = stripped[4:]
            text = _inline_format(text)
            html_lines.append(f"<h3>{text}</h3>")
            continue

        # リストアイテム
        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            text = _inline_format(stripped[2:])
            html_lines.append(f"<li>{text}</li>")
            continue

        # 空行
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("")
            continue

        # 段落
        if in_list:
            html_lines.append("</ul>")
            in_list = False
        text = _inline_format(stripped)
        html_lines.append(f"<p>{text}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def _inline_format(text: str) -> str:
    import re
    # **bold**
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # [text](url)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    return text


def upload_media(
    image_bytes: bytes,
    filename: str,
    attribution: str = "",
    config_path: str = "config.yaml",
) -> tuple[int, str] | None:
    """
    画像をWordPressメディアライブラリにアップロードする。
    Returns (media_id, source_url) or None.
    """
    config = load_config(config_path)
    wp_cfg = config.get("wordpress", {})
    base_url = os.environ.get("WP_URL", wp_cfg.get("url", "")).rstrip("/")
    ip_base_url, host_header = _resolve_to_ip(base_url)
    endpoint = f"{ip_base_url}/wp-json/wp/v2/media"

    headers = {
        **_get_auth_header(),
        **host_header,
        "Content-Type": "image/jpeg",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }

    try:
        resp = requests.post(endpoint, data=image_bytes, headers=headers, timeout=60, verify=_SSL_VERIFY)
        resp.raise_for_status()
        data = resp.json()
        media_id = data["id"]
        source_url = data.get("source_url", "")

        # 帰属情報をキャプションに設定
        if attribution:
            requests.post(
                f"{ip_base_url}/wp-json/wp/v2/media/{media_id}",
                json={"caption": attribution},
                headers={**_get_auth_header(), **host_header, "Content-Type": "application/json"},
                timeout=15,
                verify=_SSL_VERIFY,
            )

        print(f"[publisher] 画像アップロード完了 (media ID={media_id})")
        return media_id, source_url
    except Exception as e:
        print(f"[publisher] 画像アップロード失敗: {e}")
        return None


def insert_player_images(
    html: str,
    player_images: list[tuple[str, str, str]],
) -> str:
    """
    記事HTML の各 <h2> 直後に選手写真を float:right で挿入する。
    player_images: [(source_url, player_name, attribution), ...]
    """
    if not player_images:
        return html

    def make_figure(src: str, name: str, attr: str) -> str:
        return (
            f'<figure style="float:right;margin:0 0 1.2em 1.8em;max-width:220px;text-align:center;">'
            f'<img src="{src}" alt="{name}" style="width:100%;height:auto;border-radius:6px;">'
            f'<figcaption style="font-size:0.78em;color:#888;margin-top:0.3em;">'
            f'{name}<br><span style="font-size:0.85em;">{attr}</span>'
            f'</figcaption></figure>'
        )

    # <h2> の位置を先頭から順に探して画像を挿入
    result = html
    offset = 0
    for src, name, attr in player_images:
        pos = result.find("<h2", offset)
        if pos == -1:
            break
        end = result.find("</h2>", pos)
        if end == -1:
            break
        insert_at = end + len("</h2>")
        figure = make_figure(src, name, attr)
        result = result[:insert_at] + "\n" + figure + result[insert_at:]
        # 次の検索は挿入した figure の後ろから
        offset = insert_at + len(figure) + 1

    return result


def _fetch_related_posts(
    base_url: str,
    ip_base_url: str,
    host_header: dict,
    category_id: int,
    exclude_id: int,
    count: int = 4,
) -> list[dict]:
    """同カテゴリの最新記事を取得して内部リンク用データを返す"""
    try:
        resp = requests.get(
            f"{ip_base_url}/wp-json/wp/v2/posts",
            params={"categories": category_id, "per_page": count + 1,
                    "status": "publish", "orderby": "date", "order": "desc"},
            headers={**_get_auth_header(), **host_header},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        resp.raise_for_status()
        posts = [p for p in resp.json() if p["id"] != exclude_id]
        return posts[:count]
    except Exception:
        return []


# カテゴリID → アフィリエイトリンク設定
_AFFILIATE_CONFIG: dict[int, list[dict]] = {
    4: [  # match-reviews
        {"label": "EA FC（Amazon）", "kw": "EA+FC+サッカー", "store": "amazon"},
        {"label": "サッカー戦術本（Amazon）", "kw": "サッカー+戦術+本", "store": "amazon"},
    ],
    5: [  # tactics
        {"label": "サッカー戦術本（Amazon）", "kw": "サッカー+戦術+分析+本", "store": "amazon"},
        {"label": "フットボール分析（楽天）", "kw": "サッカー+戦術+本", "store": "rakuten"},
    ],
    6: [  # united
        {"label": "マンU ユニフォーム（楽天）", "kw": "マンチェスターユナイテッド+ユニフォーム", "store": "rakuten"},
        {"label": "プレミアリーググッズ（Amazon）", "kw": "プレミアリーグ+グッズ", "store": "amazon"},
    ],
    7: [  # transfers
        {"label": "プレミアリーグ ユニフォーム（楽天）", "kw": "プレミアリーグ+ユニフォーム", "store": "rakuten"},
        {"label": "サッカーグッズ（Amazon）", "kw": "サッカー+グッズ", "store": "amazon"},
    ],
    8: [  # europe
        {"label": "欧州サッカーグッズ（楽天）", "kw": "チャンピオンズリーグ+グッズ", "store": "rakuten"},
        {"label": "欧州フットボール本（Amazon）", "kw": "欧州+サッカー+本", "store": "amazon"},
        {"label": "楽天TV（UEFA CL見放題）", "kw": None, "store": "rakuten_tv"},
    ],
    9: [  # data
        {"label": "サッカーデータ分析本（Amazon）", "kw": "サッカー+データ+分析+本", "store": "amazon"},
    ],
    10: [  # column
        {"label": "フットボール本（Amazon）", "kw": "フットボール+本", "store": "amazon"},
    ],
}


def _fetch_pexels_image_url(keyword: str) -> str | None:
    """Pexelsからキーワードに関連する画像URLを取得（カード用サムネイル）"""
    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": keyword, "per_page": 5, "orientation": "landscape"},
            timeout=10,
            verify=_SSL_VERIFY,
        )
        data = r.json()
        photos = data.get("photos", [])
        if photos:
            return photos[0]["src"]["medium"]
        return None
    except Exception:
        return None


def _aff_card_icon(url: str, icon: str, brand: str, brand_color: str, title: str, desc: str, cta: str) -> str:
    """アイコン型アフィリエイトカード（商品画像なし）"""
    return (
        f'<a href="{url}" target="_blank" rel="nofollow noopener sponsored" '
        f'style="display:flex;align-items:center;gap:14px;padding:14px 16px;'
        f'border:1px solid #e0e0e0;border-radius:8px;background:#fff;'
        f'text-decoration:none;color:inherit;">'
        f'<div style="flex-shrink:0;width:56px;height:56px;border-radius:8px;'
        f'background:{brand_color};display:flex;align-items:center;justify-content:center;'
        f'font-size:26px;line-height:1;">{icon}</div>'
        f'<div style="flex:1;min-width:0;">'
        f'<div style="font-size:10px;font-weight:700;letter-spacing:.1em;color:{brand_color};margin-bottom:2px;">{brand}</div>'
        f'<div style="font-size:14px;font-weight:700;color:#111;margin-bottom:3px;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{title}</div>'
        f'<div style="font-size:12px;color:#666;">{desc}</div>'
        f'</div>'
        f'<div style="flex-shrink:0;font-size:12px;font-weight:700;color:#fff;'
        f'background:{brand_color};padding:6px 12px;border-radius:20px;white-space:nowrap;">{cta}</div>'
        f'</a>'
    )


def _aff_card_image(url: str, image_url: str, brand: str, brand_color: str,
                    title: str, desc: str, cta: str) -> str:
    """画像付きアフィリエイトカード（価格なし）"""
    return (
        f'<a href="{url}" target="_blank" rel="nofollow noopener sponsored" '
        f'style="display:flex;align-items:center;gap:14px;padding:14px 16px;'
        f'border:1px solid #e0e0e0;border-radius:8px;background:#fff;'
        f'text-decoration:none;color:inherit;">'
        f'<div style="flex-shrink:0;width:80px;height:60px;border-radius:6px;'
        f'overflow:hidden;background:#f5f5f5;">'
        f'<img src="{image_url}" width="80" height="60" '
        f'style="object-fit:cover;width:100%;height:100%;" alt="{title}" loading="lazy">'
        f'</div>'
        f'<div style="flex:1;min-width:0;">'
        f'<div style="font-size:10px;font-weight:700;letter-spacing:.1em;color:{brand_color};margin-bottom:2px;">{brand}</div>'
        f'<div style="font-size:14px;font-weight:700;color:#111;margin-bottom:3px;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{title}</div>'
        f'<div style="font-size:12px;color:#666;">{desc}</div>'
        f'</div>'
        f'<div style="flex-shrink:0;font-size:12px;font-weight:700;color:#fff;'
        f'background:{brand_color};padding:6px 12px;border-radius:20px;white-space:nowrap;">{cta}</div>'
        f'</a>'
    )


def _suggest_affiliate_keywords(title: str, content_hint: str = "") -> list[str]:
    """記事タイトル＋冒頭テキストから楽天市場向け商品キーワードを最大2つ生成する"""
    import anthropic, json as _json, httpx
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return []
    try:
        hint_block = f"\n記事冒頭: 「{content_hint[:250]}」" if content_hint else ""
        http_client = httpx.Client(verify=False) if not _SSL_VERIFY else None
        client = anthropic.Anthropic(api_key=api_key, http_client=http_client)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": (
                    f"記事タイトル: 「{title}」{hint_block}\n\n"
                    "この記事を読んだサッカーファンが思わずクリックしたくなる楽天市場の商品キーワードを2つ、JSON配列で出力。\n"
                    "ルール:\n"
                    "- 記事に登場するクラブ名・選手名・地名・人名を最優先で使う\n"
                    "- 移籍記事なら移籍先クラブのユニフォーム・グッズ、その地域の名産品も可\n"
                    "- コラム・人物記事なら関連書籍・自伝・DVD\n"
                    "- 意外性があっても読者が欲しくなるものを選ぶ（食品・雑貨・旅行グッズ等もOK）\n"
                    "- キーワードは楽天市場検索に適した自然な日本語（10文字以内）\n"
                    "例: [\"アーセナル ユニフォーム\", \"ロンドン 紅茶\"] や [\"ファーガソン 自伝\", \"マンU マフラー\"]\n"
                    "JSON配列のみ出力（説明不要）:"
                ),
            }],
        )
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return _json.loads(text.strip())
    except Exception as e:
        print(f"[publisher] キーワード生成失敗: {e}")
        return []


def _build_affiliate_html(category_id: int, topic_title: str | None = None, content_hint: str = "") -> str:
    amazon_id  = os.environ.get("AMAZON_ASSOCIATE_ID", "")
    rakuten_id = os.environ.get("RAKUTEN_AFFILIATE_ID", "")
    sptv_id    = os.environ.get("SPTV_AFFILIATE_ID", "")
    dazn_url   = os.environ.get("DAZN_AFFILIATE_URL", "")
    if not amazon_id and not rakuten_id and not sptv_id and not dazn_url:
        return ""

    # topic_titleが渡された場合はAIで動的キーワードを生成してrakutenカードに使う
    dynamic_items: list[dict] = []
    if topic_title and rakuten_id:
        kws = _suggest_affiliate_keywords(topic_title, content_hint)
        for kw in kws[:2]:
            dynamic_items.append({"label": kw, "kw": kw.replace(" ", "+"), "store": "rakuten"})

    items = dynamic_items if dynamic_items else _AFFILIATE_CONFIG.get(category_id, [
        {"label": "サッカーグッズ", "kw": "サッカー+グッズ", "store": "amazon"},
    ])

    pexels_key = os.environ.get("PEXELS_API_KEY", "")

    cards = []
    for item in items:
        if item["store"] == "amazon" and amazon_id:
            url = f"https://www.amazon.co.jp/s?k={item['kw']}&tag={amazon_id}"
            img = _fetch_pexels_image_url(item["kw"].replace("+", " ")) if pexels_key else None
            if img:
                cards.append(_aff_card_image(
                    url, img, "Amazon.co.jp", "#FF9900",
                    item["label"], "Amazonで最安値をチェック", "今すぐ見る"
                ))
            else:
                cards.append(_aff_card_icon(
                    url, "🛒", "Amazon.co.jp", "#FF9900",
                    item["label"], "Amazonで最安値をチェック", "今すぐ見る"
                ))
        elif item["store"] == "rakuten" and rakuten_id:
            kw_encoded = item["kw"].replace("+", "%20")
            url = f"https://hb.afl.rakuten.co.jp/ichiba/{rakuten_id}/?pc=https%3A%2F%2Fsearch.rakuten.co.jp%2Fsearch%2Fmall%2F{kw_encoded}%2F"
            img = _fetch_pexels_image_url(item["kw"].replace("+", " ")) if pexels_key else None
            if img:
                cards.append(_aff_card_image(
                    url, img, "楽天市場", "#BF0000",
                    item["label"], "楽天ポイントが貯まる・使える", "楽天で探す"
                ))
            else:
                cards.append(_aff_card_icon(
                    url, "🛒", "楽天市場", "#BF0000",
                    item["label"], "楽天ポイントが貯まる・使える", "楽天で探す"
                ))
        elif item["store"] == "sptv" and sptv_id:
            url = f"https://px.a8.net/svt/ejp?a8mat={sptv_id}"
            cards.append(_aff_card_icon(
                url, "📺", "スカパー!", "#003087",
                item["label"], "プレミアリーグ全試合を生中継", "無料体験"
            ))
        elif item["store"] == "rakuten_tv" and rakuten_id:
            url = f"https://hb.afl.rakuten.co.jp/ichiba/{rakuten_id}/?pc=https%3A%2F%2Ftv.rakuten.co.jp%2Fsports%2Fsoccer%2F"
            cards.append(_aff_card_icon(
                url, "📺", "楽天TV", "#BF0000",
                "UEFA CL・EL見放題", "楽天ポイントで視聴できる", "詳細を見る"
            ))

    # DAZN（全カテゴリ共通）
    if dazn_url:
        cards.append(_aff_card_icon(
            dazn_url, "📺", "DAZN", "#00E5A0",
            "プレミアリーグ全試合配信", "いつでもどこでも視聴可能", "無料体験"
        ))

    # Amazon Prime（全カテゴリ共通）
    if amazon_id:
        prime_url = f"https://www.amazon.co.jp/prime?tag={amazon_id}"
        cards.append(_aff_card_icon(
            prime_url, "▶", "Amazon Prime", "#00A8E1",
            "Prime Video スポーツ・映画見放題", "30日間無料体験あり", "無料で試す"
        ))

    if not cards:
        return ""

    cards_html = "\n".join(f'<div style="margin-bottom:10px;">{c}</div>' for c in cards)
    return (
        '<div class="affiliate-block" style="margin:2.5em 0;">'
        '<div style="font-size:11px;font-weight:700;letter-spacing:.12em;color:#999;'
        'text-transform:uppercase;margin-bottom:10px;padding-bottom:6px;'
        'border-bottom:1px solid #e8e8e8;">PR · アフィリエイト広告</div>'
        f'{cards_html}'
        '</div>'
    )


def _build_related_html(posts: list[dict]) -> str:
    if not posts:
        return ""
    items = "".join(
        f'<li><a href="{p["link"]}">{p["title"]["rendered"]}</a></li>'
        for p in posts
    )
    return f'<div class="related-posts"><h3>関連記事</h3><ul>{items}</ul></div>'


def publish_draft(
    title: str,
    content_markdown: str,
    config_path: str = "config.yaml",
    featured_media_id: int | None = None,
    inline_player_images: list[tuple[str, str, str]] | None = None,
    category_ids: list[int] | None = None,
    category_id: int | None = None,  # 後方互換
    meta_description: str = "",
) -> PublishResult:
    config = load_config(config_path)
    wp_cfg = config.get("wordpress", {})

    base_url = os.environ.get("WP_URL", wp_cfg.get("url", "")).rstrip("/")
    ip_base_url, host_header = _resolve_to_ip(base_url)
    endpoint = f"{ip_base_url}/wp-json/wp/v2/posts"
    status = wp_cfg.get("status", "draft")

    # category_ids 優先。未指定なら category_id（後方互換）→ config のデフォルト
    if category_ids is None:
        if category_id is not None:
            category_ids = [category_id]
        else:
            default_id = wp_cfg.get("category_id", None)
            category_ids = [default_id] if default_id else []

    # アフィリエイト・関連記事には主カテゴリ（先頭）を使う
    primary_cat_id = category_ids[0] if category_ids else None

    content_html = markdown_to_html(content_markdown)

    # 記事内選手写真を挿入
    if inline_player_images:
        content_html = insert_player_images(content_html, inline_player_images)

    payload: dict = {
        "title": title,
        "content": content_html,
        "status": status,
        "excerpt": meta_description,
    }
    if category_ids:
        payload["categories"] = category_ids
    if featured_media_id:
        payload["featured_media"] = featured_media_id

    headers = {
        **_get_auth_header(),
        **host_header,
        "Content-Type": "application/json",
    }

    resp = requests.post(endpoint, json=payload, headers=headers, timeout=30, verify=_SSL_VERIFY)
    resp.raise_for_status()

    data = resp.json()
    result = PublishResult(
        post_id=data["id"],
        url=data.get("link", ""),
        status=data.get("status", "draft"),
        title=data.get("title", {}).get("rendered", title),
    )

    # 内部リンク（関連記事）＋アフィリエイトブロックを記事末尾に追加
    if primary_cat_id:
        # markdownの最初の段落（250字）をヒントとして渡す
        _plain = " ".join(content_markdown.split())[:250]
        affiliate_html = _build_affiliate_html(primary_cat_id, title, _plain)
        related = _fetch_related_posts(
            base_url, ip_base_url, host_header, primary_cat_id, result.post_id
        )
        related_html = _build_related_html(related) if related else ""
        suffix = ""
        if affiliate_html:
            suffix += "\n" + affiliate_html
        if related_html:
            suffix += "\n" + related_html
        if suffix:
            updated_content = content_html + suffix
            try:
                requests.post(
                    f"{ip_base_url}/wp-json/wp/v2/posts/{result.post_id}",
                    json={"content": updated_content},
                    headers={**_get_auth_header(), **host_header, "Content-Type": "application/json"},
                    timeout=15,
                    verify=_SSL_VERIFY,
                )
                parts = []
                if affiliate_html:
                    parts.append("アフィリエイト")
                if related_html:
                    parts.append(f"関連記事{len(related)}件")
                print(f"[publisher] {' / '.join(parts)} 挿入完了")
            except Exception as e:
                print(f"[publisher] 末尾挿入失敗（続行）: {e}")

    print(f"[publisher] 投稿完了 (ID={result.post_id}, status={result.status}): {result.url}")
    return result


if __name__ == "__main__":
    result = publish_draft(
        title="テスト投稿",
        content_markdown="# テスト\n\nこれはテスト投稿です。\n\n## まとめ\n\nテスト完了。",
    )
    print(f"Post ID: {result.post_id}")
    print(f"URL: {result.url}")
