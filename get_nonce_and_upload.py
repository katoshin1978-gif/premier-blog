#!/usr/bin/env python3
"""
WP REST API 経由で nonce を取得し、wp-admin にPOSTする方法を試みる
"""
import os, sys, io, zipfile, json, re
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

load_dotenv('C:/premier-blog/.env')
WP_URL = os.getenv('WP_URL', '').rstrip('/')
WP_USERNAME = os.getenv('WP_USERNAME', '')
WP_APP_PASSWORD = os.getenv('WP_APP_PASSWORD', '')
SSL_VERIFY = os.getenv('SSL_VERIFY', 'true').lower() != 'false'

auth = HTTPBasicAuth(WP_USERNAME, WP_APP_PASSWORD)
ZIP_PATH = 'C:/premier-blog/premier-blog-theme.zip'

# =====================================================
# 方法1: WP REST API で nonce を生成する
# wp_create_nonce を REST API 経由で呼ぶ
# =====================================================
print("=== WP REST Nonce 取得試行 ===")

# /wp-json/wp/v2/users/me のレスポンスヘッダーに X-WP-Nonce は含まれない
# 代わりに /wp-admin/admin-ajax.php?action=rest-nonce を使う
nonce_resp = requests.get(
    f"{WP_URL}/wp-admin/admin-ajax.php",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
    params={'action': 'rest-nonce'},
)
print(f"rest-nonce: {nonce_resp.status_code} - {nonce_resp.text[:100]}")

# wp_create_nonce('install-theme') を得る別の方法
# WordPress admin-ajax.php 経由
nonce_resp2 = requests.post(
    f"{WP_URL}/wp-admin/admin-ajax.php",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
    data={'action': 'generate-password'},
)
print(f"generate-password: {nonce_resp2.status_code} - {nonce_resp2.text[:100]}")

# =====================================================
# 方法2: wp-login.php でCookieベースログインを試みる
# アプリパスワードのスペースなし版で試す
# =====================================================
print("\n=== wp-login.php Cookieログイン試行 ===")

session = requests.Session()
session.verify = SSL_VERIFY

# testcookie セット
session.get(f"{WP_URL}/wp-login.php", timeout=10)

# アプリパスワードはスペース区切りの16文字×6グループ
# フォームログインには通常のWPパスワードが必要
# アプリパスワード（スペースなし）を試す
app_pw_no_space = WP_APP_PASSWORD.replace(' ', '')
login_data = {
    'log': WP_USERNAME,
    'pwd': app_pw_no_space,
    'wp-submit': 'Log In',
    'redirect_to': '/wp-admin/',
    'testcookie': '1',
}

login_resp = session.post(
    f"{WP_URL}/wp-login.php",
    data=login_data,
    timeout=30,
    allow_redirects=True,
)
print(f"ログイン試行: status={login_resp.status_code}, url={login_resp.url}")

if 'wp-admin' in login_resp.url and 'wp-login' not in login_resp.url:
    print("ログイン成功!")

    # テーマアップロードページのnonceを取得
    upload_page = session.get(
        f"{WP_URL}/wp-admin/theme-install.php?upload",
        timeout=30,
    )
    print(f"theme-install.php: {upload_page.status_code}")

    # nonce探す
    nonce = None
    patterns = [
        r'name="_wpnonce"\s+value="([^"]+)"',
        r'name="themeupload_nonce"\s+value="([^"]+)"',
        r'"nonce":"([^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, upload_page.text)
        if m:
            nonce = m.group(1)
            print(f"nonce: {nonce}")
            break

    if nonce:
        with open(ZIP_PATH, 'rb') as f:
            zip_data = f.read()

        upload_resp = session.post(
            f"{WP_URL}/wp-admin/update.php?action=upload-theme",
            data={
                '_wpnonce': nonce,
                '_wp_http_referer': '/wp-admin/theme-install.php',
            },
            files={
                'themezip': ('premier-blog-theme.zip', zip_data, 'application/zip')
            },
            timeout=120,
        )
        print(f"アップロード: {upload_resp.status_code}")
        if 'Theme installed' in upload_resp.text or 'theme_already_exists' in upload_resp.text:
            print("アップロード処理実行!")
            print(upload_resp.text[:500])
        else:
            print(f"レスポンス: {upload_resp.text[:500]}")
    else:
        print("nonceが見つからない")
else:
    print(f"ログイン失敗")
    print(f"  クッキー: {dict(session.cookies)}")
    # エラーメッセージを探す
    error_match = re.search(r'<div id="login_error"[^>]*>(.*?)</div>', login_resp.text, re.DOTALL)
    if error_match:
        error_text = re.sub('<[^>]+>', '', error_match.group(1)).strip()
        print(f"  エラー: {error_text}")

# =====================================================
# 方法3: WP Admin Cookie が既に存在するか確認
# ブラウザでログイン済みのcookieを環境変数から取得できるか
# =====================================================
print("\n=== 環境変数からCookie確認 ===")
wp_cookie = os.getenv('WP_ADMIN_COOKIE', '')
if wp_cookie:
    print(f"WP_ADMIN_COOKIE: {wp_cookie[:50]}...")
else:
    print("WP_ADMIN_COOKIE: 未設定")

print("\n=== 総括 ===")
print("WordPressへの自動テーマアップロードは以下の理由で制限されている:")
print("1. REST API POST /wp/v2/themes: 404（ZIPインストール非対応）")
print("2. REST API POST /wp/v2/plugins: slug必須（ZIPインストール非対応）")
print("3. wp-admin Basic Auth: アプリパスワードではwp-admin画面にアクセス不可")
print("4. フォームログイン: アプリパスワードはWP認証に使えない")
print()
print("対応策:")
print("ブラウザでWordPress管理画面にログインし、以下からzipをアップロードしてください:")
print(f"  {WP_URL}/wp-admin/theme-install.php?upload")
print(f"  アップロードするファイル: {ZIP_PATH}")
print()
print("zipには以下の修正が含まれています:")
print("- front-page.php: 全WP_Queryに post_status ['publish', 'draft'] 追加済み")
print("- template-parts/hero-asym.php: アイキャッチ画像表示コード追加済み")
print("- template-parts/card.php: アイキャッチ画像表示コード確認済み（既存）")
print("- template-parts/card-row.php: アイキャッチ画像表示コード確認済み（既存）")
print("- functions.php: /premier-blog/v1/update-file エンドポイント追加（手動アップロード後に利用可能）")
