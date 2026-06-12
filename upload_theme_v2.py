#!/usr/bin/env python3
"""
WordPress テーマアップロード v2
- WP REST API /wp-json/wp/v2/themes は404なのでスキップ
- WP Admin フォームログイン（Cookieセッション）は失敗するため、
  Plugin経由のカスタムエンドポイントまたは直接FTPを検討
- まずは認証状況とロールを詳しく調べる
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

auth = HTTPBasicAuth(WP_USERNAME, WP_APP_PASSWORD)

print("=== ユーザー情報（詳細）===")
resp = requests.get(
    f"{WP_URL}/wp-json/wp/v2/users/me",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
    params={'context': 'edit'}
)
print(f"ステータス: {resp.status_code}")
if resp.status_code == 200:
    data = resp.json()
    print(json.dumps({k: v for k, v in data.items() if k in ['id','name','roles','capabilities','email']}, ensure_ascii=False, indent=2))
else:
    print(resp.text[:500])

print()
print("=== テーマ一覧確認 ===")
resp2 = requests.get(
    f"{WP_URL}/wp-json/wp/v2/themes",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
)
print(f"ステータス: {resp2.status_code}")
if resp2.status_code == 200:
    themes = resp2.json()
    for t in themes:
        print(f"  - {t.get('stylesheet','?')}: {t.get('name','?')} (status={t.get('status','?')})")
else:
    print(resp2.text[:300])

print()
print("=== プラグイン一覧 ===")
resp3 = requests.get(
    f"{WP_URL}/wp-json/wp/v2/plugins",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
)
print(f"ステータス: {resp3.status_code}")
if resp3.status_code == 200:
    plugins = resp3.json()
    for p in plugins:
        print(f"  - {p.get('plugin','?')}: status={p.get('status','?')}")
else:
    print(resp3.text[:300])

print()
print("=== WP REST API ルート一覧（テーマ関連）===")
resp4 = requests.get(
    f"{WP_URL}/wp-json/",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
)
if resp4.status_code == 200:
    routes = resp4.json().get('routes', {})
    for route in routes:
        if 'theme' in route.lower() or 'plugin' in route.lower() or 'install' in route.lower():
            print(f"  {route}")
else:
    print(f"取得失敗: {resp4.status_code}")
