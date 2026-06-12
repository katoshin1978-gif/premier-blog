#!/usr/bin/env python3
"""
WordPress テーマアップロード v5
- theme-editor.php でnonce取得→個別ファイル更新
- Basic Auth でtheme-editor.phpが200を返すのでnonceを取得してPOSTする
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

# zipからファイルを読み込む
print("ZIPからファイル読み込み中...")
zip_contents = {}
with zipfile.ZipFile(ZIP_PATH, 'r') as z:
    for name in z.namelist():
        zip_contents[name] = z.read(name).decode('utf-8')
        print(f"  読み込み: {name}")

print(f"\n合計 {len(zip_contents)} ファイル")

# theme-editor.php でファイルを更新する関数
def update_theme_file(filename, content):
    """
    WordPress theme-editor.php を使ってテーマファイルを更新する
    """
    print(f"\n  更新中: {filename}")

    # 1. GETしてnonceを取得
    get_resp = requests.get(
        f"{WP_URL}/wp-admin/theme-editor.php",
        auth=auth,
        verify=SSL_VERIFY,
        timeout=30,
        params={
            'file': filename,
            'theme': 'premier-blog-theme',
        }
    )
    print(f"  GET status: {get_resp.status_code}")

    if get_resp.status_code != 200:
        print(f"  GETに失敗")
        return False

    # ログインページにリダイレクトされているか確認
    if 'loginform' in get_resp.text or 'wp-login' in str(get_resp.url):
        print(f"  -> ログインページ（Basic Authが効いていない）")
        return False

    # nonceを探す
    nonce = None
    patterns = [
        r'name="nonce"\s+value="([^"]+)"',
        r'"nonce":"([^"]+)"',
        r"'nonce':'([^']+)'",
        r'nonce\s*=\s*"([^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, get_resp.text)
        if m:
            nonce = m.group(1)
            print(f"  nonce: {nonce}")
            break

    if not nonce:
        print("  nonceが見つからない")
        return False

    # 2. POSTしてファイルを更新
    post_resp = requests.post(
        f"{WP_URL}/wp-admin/theme-editor.php",
        auth=auth,
        verify=SSL_VERIFY,
        timeout=60,
        data={
            'nonce': nonce,
            'action': 'edit-theme-plugin-file',
            'file': filename,
            'theme': 'premier-blog-theme',
            'newcontent': content,
            'scrollto': '0',
        }
    )
    print(f"  POST status: {post_resp.status_code}")

    # レスポンスを確認
    if post_resp.status_code == 200:
        resp_text = post_resp.text
        if '"success":true' in resp_text or 'success' in resp_text.lower():
            print(f"  -> 更新成功!")
            return True
        elif 'error' in resp_text.lower():
            print(f"  -> エラー: {resp_text[:200]}")
            return False
        else:
            print(f"  -> レスポンス: {resp_text[:200]}")
            return True  # 200なら成功とみなす
    else:
        print(f"  -> 失敗: {post_resp.text[:200]}")
        return False

# まず admin-ajax.php でテーマファイルを更新する方法を試す
print("\n=== admin-ajax.php でのファイル更新 ===")

# まずnonceを取得
nonce_resp = requests.get(
    f"{WP_URL}/wp-admin/theme-editor.php",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
    params={
        'file': 'front-page.php',
        'theme': 'premier-blog-theme',
    }
)

nonce = None
for pat in [r'name="nonce"\s+value="([^"]+)"', r'"nonce":"([^"]+)"']:
    m = re.search(pat, nonce_resp.text)
    if m:
        nonce = m.group(1)
        break

print(f"nonce: {nonce}")

if nonce:
    # admin-ajax.php 経由でファイル更新
    with zipfile.ZipFile(ZIP_PATH, 'r') as z:
        front_page_content = z.read('front-page.php').decode('utf-8')

    ajax_resp = requests.post(
        f"{WP_URL}/wp-admin/admin-ajax.php",
        auth=auth,
        verify=SSL_VERIFY,
        timeout=60,
        data={
            'action': 'edit-theme-plugin-file',
            'nonce': nonce,
            'file': 'front-page.php',
            'theme': 'premier-blog-theme',
            'newcontent': front_page_content,
            'scrollto': '0',
        }
    )
    print(f"ajax POST status: {ajax_resp.status_code}")
    print(f"ajax レスポンス: {ajax_resp.text[:300]}")

print()
print("=== theme-editor.php 直接POSTでファイル更新 ===")

# 更新対象ファイル（変更が入ったもの）
TARGET_FILES = [
    'front-page.php',
    'template-parts/hero-asym.php',
    'template-parts/card.php',
    'template-parts/card-row.php',
]

success_count = 0
fail_count = 0

for target_file in TARGET_FILES:
    if target_file in zip_contents:
        ok = update_theme_file(target_file, zip_contents[target_file])
        if ok:
            success_count += 1
        else:
            fail_count += 1
    else:
        print(f"  {target_file}: zipに存在しない")

print(f"\n=== 結果 ===")
print(f"成功: {success_count} / {len(TARGET_FILES)}")
print(f"失敗: {fail_count} / {len(TARGET_FILES)}")
