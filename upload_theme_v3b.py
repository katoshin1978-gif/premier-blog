#!/usr/bin/env python3
"""
WordPress テーマアップロード v3b - 文字化け対策版
"""
import os
import sys
import io
import requests
import json
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# stdout を utf-8 に設定
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

load_dotenv('C:/premier-blog/.env')

WP_URL      = os.getenv('WP_URL', '').rstrip('/')
WP_USERNAME = os.getenv('WP_USERNAME', '')
WP_APP_PASSWORD = os.getenv('WP_APP_PASSWORD', '')
SSL_VERIFY  = os.getenv('SSL_VERIFY', 'true').lower() != 'false'

ZIP_PATH = 'C:/premier-blog/premier-blog-theme.zip'

auth = HTTPBasicAuth(WP_USERNAME, WP_APP_PASSWORD)

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
print(f"レスポンス: {resp.text[:800]}")

if resp.status_code in [200, 201]:
    print("アップロード成功!")
    sys.exit(0)

# 方法2: raw binary + Content-Disposition
print("\n-- 方法2: raw binary upload --")
resp2 = requests.post(
    f"{WP_URL}/wp-json/wp/v2/themes",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=120,
    headers={
        'Content-Type': 'application/zip',
        'Content-Disposition': 'attachment; filename="premier-blog-theme.zip"',
    },
    data=zip_data
)
print(f"ステータス: {resp2.status_code}")
print(f"レスポンス: {resp2.text[:800]}")

if resp2.status_code in [200, 201]:
    print("アップロード成功!")
    sys.exit(0)

# テーマの現在の状態確認
print("\n-- 現在のテーマ情報 --")
resp3 = requests.get(
    f"{WP_URL}/wp-json/wp/v2/themes/premier-blog-theme",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
)
print(f"ステータス: {resp3.status_code}")
if resp3.status_code == 200:
    t = resp3.json()
    print(f"テーマ名: {t.get('name', {}).get('rendered','?')}")
    print(f"バージョン: {t.get('version', '?')}")
    print(f"ステータス: {t.get('status', '?')}")
    print(f"テンプレートパーツURL等の詳細:")
    for k in ['author', 'version', 'status', 'theme_uri', 'theme_supports']:
        if k in t:
            print(f"  {k}: {t[k]}")
