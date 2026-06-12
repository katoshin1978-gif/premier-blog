"""
WordPressセットアップスクリプト。
- プライバシーポリシー固定ページを作成
- ナビゲーションメニューを作成（ホーム + カテゴリ + プライバシーポリシー）
- メニュー項目を追加
実行後、WP管理画面で「外観 > メニュー」からメニューをテーマに割り当てること。
"""

import os
import base64
import socket
from urllib.parse import urlparse, urlunparse

import requests
import urllib3
import yaml
from dotenv import load_dotenv

load_dotenv()

_SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() != "false"
if not _SSL_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PRIVACY_POLICY_CONTENT = """
<h2>プライバシーポリシー</h2>
<p>本サイト（以下「当サイト」）は、ユーザーのプライバシーを尊重し、個人情報の保護に努めます。</p>

<h3>1. 収集する情報</h3>
<p>当サイトでは、アクセス解析・広告配信の目的でCookieおよびこれに類する技術を使用します。これらはブラウザの設定により無効化できます。</p>

<h3>2. Google AdSense（広告）</h3>
<p>当サイトはGoogle AdSenseを利用した広告を掲載しています。GoogleはCookieを使用して、ユーザーの興味に基づいた広告を表示します。Googleによる広告Cookieの使用は、<a href="https://policies.google.com/technologies/ads" target="_blank" rel="noopener">Googleの広告ポリシー</a>に従います。</p>
<p>Google広告のオプトアウトは<a href="https://www.google.com/settings/ads" target="_blank" rel="noopener">広告設定ページ</a>から行えます。</p>

<h3>3. アフィリエイトプログラム</h3>
<p>当サイトは以下のアフィリエイトプログラムに参加しています。</p>
<ul>
<li><strong>Amazonアソシエイト</strong>：Amazon.co.jpの商品を紹介し、適格販売により収入を得ることがあります。</li>
<li><strong>楽天アフィリエイト</strong>：楽天市場の商品を紹介し、成果報酬を得ることがあります。</li>
</ul>
<p>商品リンクをクリックした場合、当サイトに紹介料が入ることがありますが、ユーザーへの追加費用は一切発生しません。</p>

<h3>4. アクセス解析</h3>
<p>当サイトはGoogle Analyticsを使用してアクセス状況を解析しています。取得データはGoogleのプライバシーポリシーに基づき管理されます。詳細は<a href="https://policies.google.com/privacy" target="_blank" rel="noopener">Googleプライバシーポリシー</a>をご覧ください。</p>

<h3>5. 免責事項</h3>
<p>当サイトの記事はAIが生成したコンテンツを含みます。情報の正確性・完全性については保証できません。最新情報は公式サイトでご確認ください。当サイトの情報を利用したことによる損害について、当サイトは責任を負いません。</p>

<h3>6. 著作権</h3>
<p>当サイトに掲載された記事・画像の著作権は各権利者に帰属します。無断転載・複製を禁じます。</p>

<h3>7. プライバシーポリシーの変更</h3>
<p>本ポリシーは予告なく変更することがあります。変更後は本ページに掲載します。</p>

<h3>8. お問い合わせ</h3>
<p>プライバシーに関するお問い合わせは katoshin1978@gmail.com までご連絡ください。</p>
"""

CATEGORY_TABS = [
    {"label": "試合レビュー", "id": 4,  "slug": "match-reviews"},
    {"label": "移籍",         "id": 7,  "slug": "transfers"},
    {"label": "マンU",        "id": 6,  "slug": "united"},
    {"label": "欧州",         "id": 8,  "slug": "europe"},
    {"label": "データ",       "id": 9,  "slug": "data"},
    {"label": "コラム",       "id": 10, "slug": "column"},
]


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_auth_header() -> dict:
    token = base64.b64encode(
        f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()
    ).decode()
    return {"Authorization": f"Basic {token}"}


def resolve_ip(url: str) -> tuple:
    parsed = urlparse(url)
    try:
        ip = socket.gethostbyname(parsed.hostname)
        port = f":{parsed.port}" if parsed.port else ""
        new_url = urlunparse(parsed._replace(netloc=f"{ip}{port}"))
        return new_url, {"Host": parsed.hostname}
    except Exception:
        return url, {}


def create_privacy_policy_page(base_url, ip_base, host_hdr):
    print("[setup] プライバシーポリシーページを作成中...")
    resp = requests.post(
        f"{ip_base}/wp-json/wp/v2/pages",
        json={
            "title": "プライバシーポリシー",
            "content": PRIVACY_POLICY_CONTENT,
            "status": "publish",
            "slug": "privacy-policy",
        },
        headers={**get_auth_header(), **host_hdr, "Content-Type": "application/json"},
        timeout=30,
        verify=_SSL_VERIFY,
    )
    resp.raise_for_status()
    page = resp.json()
    page_id = page["id"]
    page_url = page.get("link", f"{base_url}/privacy-policy/")
    print(f"[setup] ページ作成完了 (ID={page_id}): {page_url}")
    return page_id, page_url


def get_category_url(ip_base, host_hdr, cat_id):
    resp = requests.get(
        f"{ip_base}/wp-json/wp/v2/categories/{cat_id}",
        headers={**get_auth_header(), **host_hdr},
        timeout=15,
        verify=_SSL_VERIFY,
    )
    resp.raise_for_status()
    return resp.json().get("link", "")


def create_menu(ip_base, host_hdr):
    print("[setup] ナビゲーションメニューを作成中...")
    resp = requests.post(
        f"{ip_base}/wp-json/wp/v2/menus",
        json={"name": "メインメニュー", "slug": "main-menu"},
        headers={**get_auth_header(), **host_hdr, "Content-Type": "application/json"},
        timeout=15,
        verify=_SSL_VERIFY,
    )
    resp.raise_for_status()
    menu_id = resp.json()["id"]
    print(f"[setup] メニュー作成完了 (ID={menu_id})")
    return menu_id


def add_menu_item(ip_base, host_hdr, menu_id, item):
    resp = requests.post(
        f"{ip_base}/wp-json/wp/v2/menu-items",
        json={**item, "status": "publish", "menus": menu_id},
        headers={**get_auth_header(), **host_hdr, "Content-Type": "application/json"},
        timeout=15,
        verify=_SSL_VERIFY,
    )
    resp.raise_for_status()
    print(f"[setup]   追加: {item.get('title', '?')}")


def main():
    config = load_config()
    wp_cfg = config.get("wordpress", {})
    base_url = os.environ.get("WP_URL", wp_cfg.get("url", "")).rstrip("/")
    ip_base, host_hdr = resolve_ip(base_url)

    # 1. プライバシーポリシーページ作成
    page_id, page_url = create_privacy_policy_page(base_url, ip_base, host_hdr)

    # 2. カテゴリURLを取得
    print("[setup] カテゴリURLを取得中...")
    cat_urls = {}
    for cat in CATEGORY_TABS:
        try:
            cat_urls[cat["id"]] = get_category_url(ip_base, host_hdr, cat["id"])
        except Exception as e:
            cat_urls[cat["id"]] = f"{base_url}/?cat={cat['id']}"
            print(f"[setup]   カテゴリ{cat['id']} URL取得失敗（フォールバック使用）: {e}")

    # 3. メニュー作成
    try:
        menu_id = create_menu(ip_base, host_hdr)
    except Exception as e:
        print(f"\n[setup] メニューAPIが使えません: {e}")
        print("\n=== 手動設定手順 ===")
        print("WP管理画面 > 外観 > メニュー から以下を設定してください：")
        print("  1. 新規メニューを作成（名前: メインメニュー）")
        print("  2. カスタムリンクでホームを追加: /")
        for cat in CATEGORY_TABS:
            print(f"  3. カテゴリ「{cat['label']}」を追加 (ID: {cat['id']})")
        print(f"  4. 固定ページ「プライバシーポリシー」を追加 (ID: {page_id})")
        print("  5. 表示場所をヘッダーに設定")
        return

    # 4. メニュー項目を追加
    print("[setup] メニュー項目を追加中...")

    add_menu_item(ip_base, host_hdr, menu_id, {
        "title": "ホーム",
        "url": base_url + "/",
        "type": "custom",
        "menu_order": 1,
    })

    for order, cat in enumerate(CATEGORY_TABS, start=2):
        add_menu_item(ip_base, host_hdr, menu_id, {
            "title": cat["label"],
            "url": cat_urls.get(cat["id"], f"{base_url}/?cat={cat['id']}"),
            "type": "taxonomy",
            "object": "category",
            "object_id": cat["id"],
            "menu_order": order,
        })

    add_menu_item(ip_base, host_hdr, menu_id, {
        "title": "プライバシーポリシー",
        "url": page_url,
        "type": "post_type",
        "object": "page",
        "object_id": page_id,
        "menu_order": len(CATEGORY_TABS) + 2,
    })

    # 5. メニューを primary ロケーションに割り当て
    print("[setup] メニューを primary ロケーションに割り当て中...")
    try:
        resp = requests.put(
            f"{ip_base}/wp-json/wp/v2/menus/{menu_id}",
            json={"locations": ["primary"]},
            headers={**get_auth_header(), **host_hdr, "Content-Type": "application/json"},
            timeout=15,
            verify=_SSL_VERIFY,
        )
        resp.raise_for_status()
        print("[setup] ロケーション割り当て完了")
    except Exception as e:
        print(f"[setup] ロケーション割り当て失敗（手動設定が必要）: {e}")

    print(f"""
=== 完了 ===
プライバシーポリシー: {page_url}
メニューID: {menu_id}
ナビゲーション: ホーム / 試合レビュー / 移籍 / マンU / 欧州 / データ / コラム / プライバシーポリシー
""")


if __name__ == "__main__":
    main()
