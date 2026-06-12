#!/usr/bin/env python3
"""
WordPress テーマアップロード v3
- REST API POST /wp/v2/themes でのzip送信
- 既存テーマの場合は上書き対応
"""
import os
import sys
import requests
import json
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv('C:/premier-blog/.env')

WP_URL      = os.getenv('WP_URL', '').rstrip('/')
WP_USERNAME = os.getenv('WP_USERNAME', '')
WP_APP_PASSWORD = os.getenv('WP_APP_PASSWORD', '')
SSL_VERIFY  = os.getenv('SSL_VERIFY', 'true').lower() != 'false'

ZIP_PATH = 'C:/premier-blog/premier-blog-theme.zip'

auth = HTTPBasicAuth(WP_USERNAME, WP_APP_PASSWORD)

print("=== REST API でテーマZIPアップロード試行 ===")

# WP 5.5+ の正しいテーマアップロード API
# POST /wp-json/wp/v2/themes
# Content-Disposition: attachment; filename="theme.zip"
# Content-Type: application/zip

with open(ZIP_PATH, 'rb') as f:
    zip_data = f.read()

print(f"ZIPサイズ: {len(zip_data)} bytes")

# 方法1: multipart/form-data
print("\n-- 方法1: multipart/form-data --")
resp = requests.post(
    f"{WP_URL}/wp-json/wp/v2/themes",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=120,
    files={
        'file': ('premier-blog-theme.zip', zip_data, 'application/zip')
    },
    data={'overwrite': True}
)
print(f"ステータス: {resp.status_code}")
print(f"レスポンス: {resp.text[:500]}")

if resp.status_code in [200, 201]:
    print("✓ アップロード成功！")
    sys.exit(0)

# 方法2: raw body + Content-Disposition ヘッダー
print("\n-- 方法2: raw binary upload --")
resp2 = requests.post(
    f"{WP_URL}/wp-json/wp/v2/themes",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=120,
    headers={
        'Content-Type': 'application/zip',
        'Content-Disposition': 'attachment; filename="premier-blog-theme.zip"',
        'X-WP-Nonce': '',
    },
    data=zip_data
)
print(f"ステータス: {resp2.status_code}")
print(f"レスポンス: {resp2.text[:500]}")

if resp2.status_code in [200, 201]:
    print("✓ アップロード成功！")
    sys.exit(0)

# 方法3: nonce付きでリトライ
print("\n-- 方法3: nonceを取得してリトライ --")

# nonce取得
nonce_resp = requests.get(
    f"{WP_URL}/wp-json/",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
)
# nonce は通常 REST API コール後のレスポンスヘッダーから取得
# または /wp-json/?_wpnonce=... で要求

# wp-admin の update.php に直接POSTする方法を試みる
print("\n-- 方法4: WP Admin update.php 直接POST (Basic Auth) --")

# Basic Auth付きでadminページにアクセス
session = requests.Session()

# まずnonceを取得
admin_resp = requests.get(
    f"{WP_URL}/wp-admin/admin-ajax.php",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
    params={
        'action': 'rest-nonce',
    }
)
print(f"nonce ajax: {admin_resp.status_code}, {admin_resp.text[:200]}")

# wp-admin theme-install.php はCookieセッションが必要なのでBasic Authでは動かない
# 代わりにWP-CLIやSSHが必要
# WordPress本体のauto-updateメカニズムを使う

# 方法5: WordPress の wp-json/wp/v2/themes/{stylesheet} PUT でテーマ設定変更
# これは既存テーマのstatus変更のみ可能（ファイル上書きは不可）

print("\n=== 代替手段: themes/{stylesheet} の情報確認 ===")
resp5 = requests.get(
    f"{WP_URL}/wp-json/wp/v2/themes/premier-blog-theme",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
)
print(f"ステータス: {resp5.status_code}")
if resp5.status_code == 200:
    theme_info = resp5.json()
    print(json.dumps({k: v for k, v in theme_info.items() if k not in ['screenshot', 'description', 'tags']}, ensure_ascii=False, indent=2))

# 方法6: wp-json/wp/v2/themes にFile Upload API を使う（WordPress 5.5+のtheme install）
print("\n-- 方法6: WordPress 5.5+ Theme Install API --")
# WordPress 5.5+ supports theme installation via REST API
# See: https://make.wordpress.org/core/2020/07/29/rest-api-changes-in-5-5/

resp6 = requests.post(
    f"{WP_URL}/wp-json/wp/v2/themes",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=120,
    headers={
        'Content-Type': 'application/zip',
        'Content-Disposition': 'attachment; filename=premier-blog-theme.zip',
    },
    data=zip_data
)
print(f"ステータス: {resp6.status_code}")
print(f"レスポンス: {resp6.text[:800]}")
