#!/usr/bin/env python3
"""
WordPress テーマアップロード v4
- WP Admin Basic Auth でtheme-editor APIを使い個別ファイルを更新
- または theme-install.php の nonce を REST API で取得して使う
"""
import os
import sys
import io
import re
import requests
import zipfile
import json
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

load_dotenv('C:/premier-blog/.env')

WP_URL      = os.getenv('WP_URL', '').rstrip('/')
WP_USERNAME = os.getenv('WP_USERNAME', '')
WP_APP_PASSWORD = os.getenv('WP_APP_PASSWORD', '')
SSL_VERIFY  = os.getenv('SSL_VERIFY', 'true').lower() != 'false'

ZIP_PATH = 'C:/premier-blog/premier-blog-theme.zip'

auth = HTTPBasicAuth(WP_USERNAME, WP_APP_PASSWORD)

# =====================================================
# 方法A: WP Admin Theme Editor API (Basic Auth)
# /wp-admin/admin-ajax.php action=update-theme-editor
# =====================================================
print("=== 方法A: Theme Editor (admin-ajax.php) ===")

# まず nonce を REST API 経由で取得
nonce_resp = requests.get(
    f"{WP_URL}/wp-json/",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
)
# WP REST API nonce を取得する別の方法
# wp_create_nonce('wp_rest') を返すエンドポイント

# admin-ajax.php で nonce を取得
ajax_nonce_resp = requests.post(
    f"{WP_URL}/wp-admin/admin-ajax.php",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
    data={
        'action': 'rest_nonce',
    }
)
print(f"nonce (ajax): {ajax_nonce_resp.status_code} - {ajax_nonce_resp.text[:100]}")

# =====================================================
# 方法B: WP Admin テーマエディタ経由でファイル更新
# POST /wp-admin/theme-editor.php で個別PHPファイルを更新
# =====================================================
print("\n=== 方法B: Theme Editor で個別ファイル更新 ===")

# まずtheme-editor.phpにGETしてnonceを取得
editor_resp = requests.get(
    f"{WP_URL}/wp-admin/theme-editor.php",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
    params={
        'file': 'front-page.php',
        'theme': 'premier-blog-theme',
    }
)
print(f"theme-editor.php GET: {editor_resp.status_code}")

nonce = None
if editor_resp.status_code == 200:
    # nonce を探す
    nonce_patterns = [
        r'name="nonce"\s+value="([^"]+)"',
        r'"nonce"\s*:\s*"([^"]+)"',
        r'_wpnonce.*?value="([^"]+)"',
        r'name="_wpnonce"\s+value="([^"]+)"',
    ]
    for pat in nonce_patterns:
        m = re.search(pat, editor_resp.text)
        if m:
            nonce = m.group(1)
            print(f"Nonce発見: {nonce}")
            break

    if not nonce:
        print("Nonceが見つからない")
        # ページの中身の一部を確認
        # ログインページにリダイレクトされているかチェック
        if 'loginform' in editor_resp.text or 'wp-login' in editor_resp.url:
            print("-> ログインページにリダイレクトされた（Basic Authが機能していない）")
        else:
            # nonceを探す別の方法
            import re
            all_nonces = re.findall(r'[a-f0-9]{10}', editor_resp.text)
            print(f"10文字の16進数候補: {all_nonces[:5]}")
            # フォームの詳細を調べる
            forms = re.findall(r'<form[^>]*>.*?</form>', editor_resp.text, re.DOTALL)
            for i, form in enumerate(forms[:2]):
                print(f"フォーム{i}: {form[:300]}")

# =====================================================
# 方法C: wp-admin Basic Authがサポートされているか確認
# =====================================================
print("\n=== 方法C: wp-admin Basic Auth確認 ===")
admin_test = requests.get(
    f"{WP_URL}/wp-admin/",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
    allow_redirects=False
)
print(f"wp-admin/ (Basic Auth): {admin_test.status_code}, Location={admin_test.headers.get('Location','')}")

# Cookieベースのログインを試みる（アプリパスワードは通常のログインには使えない）
# WPはアプリパスワードでwp-admin/には入れない（REST APIのみ）
# これはWordPressの仕様

# =====================================================
# 方法D: テーマフォルダへの直接ファイル配置（FTP/SSH）
# 環境変数にFTP情報があるか確認
# =====================================================
print("\n=== 方法D: FTP/SSH設定確認 ===")
ftp_host = os.getenv('FTP_HOST', '')
ftp_user = os.getenv('FTP_USERNAME', '')
ssh_host = os.getenv('SSH_HOST', '')
print(f"FTP_HOST: {ftp_host or '未設定'}")
print(f"FTP_USERNAME: {ftp_user or '未設定'}")
print(f"SSH_HOST: {ssh_host or '未設定'}")

# =====================================================
# 方法E: WordPress の Filesystem API を使うカスタムエンドポイント
# （プラグインが必要）
# =====================================================
print("\n=== 方法E: カスタムRESTエンドポイント確認 ===")
routes_resp = requests.get(
    f"{WP_URL}/wp-json/",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
)
if routes_resp.status_code == 200:
    routes = routes_resp.json().get('routes', {})
    custom_routes = [r for r in routes if not r.startswith('/wp/') and not r.startswith('/oembed/')]
    print(f"カスタムルート ({len(custom_routes)}件):")
    for r in custom_routes[:20]:
        print(f"  {r}")

# =====================================================
# 結論と代替手段の提示
# =====================================================
print("\n=== 結論 ===")
print("WordPress REST API でのテーマZIPアップロードは")
print("このサーバー設定ではサポートされていない。")
print()
print("現在のzipファイルの状態:")
print(f"  パス: {ZIP_PATH}")
import os
print(f"  サイズ: {os.path.getsize(ZIP_PATH)} bytes")

print()
print("代替手段:")
print("1. WordPress管理画面（ブラウザ）から手動でアップロード")
print(f"   URL: {WP_URL}/wp-admin/theme-install.php?upload")
print("2. FTP/SSH でサーバーに直接配置")
print("3. cPanel/Plesk のファイルマネージャーを使用")
