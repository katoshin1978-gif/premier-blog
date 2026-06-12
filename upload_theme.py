#!/usr/bin/env python3
"""
WordPress テーマzipアップロードスクリプト
前のエージェントで修正済みのzipをWPにアップロードする
"""
import os
import sys
import zipfile
import io
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# .env 読み込み
load_dotenv('C:/premier-blog/.env')

WP_URL      = os.getenv('WP_URL', '').rstrip('/')
WP_USERNAME = os.getenv('WP_USERNAME', '')
WP_APP_PASSWORD = os.getenv('WP_APP_PASSWORD', '')
SSL_VERIFY  = os.getenv('SSL_VERIFY', 'true').lower() != 'false'

ZIP_PATH = 'C:/premier-blog/premier-blog-theme.zip'

print(f"WP_URL: {WP_URL}")
print(f"WP_USERNAME: {WP_USERNAME}")
print(f"ZIP_PATH: {ZIP_PATH}")
print(f"SSL_VERIFY: {SSL_VERIFY}")
print()

# ----------------------------
# ステップ1: zipの中身を確認
# ----------------------------
print("=== ZIP内容確認 ===")
with zipfile.ZipFile(ZIP_PATH, 'r') as z:
    names = z.namelist()
    for n in names:
        print(f"  {n}")
print()

# ----------------------------
# ステップ2: WordPress REST API でテーマアップロードを試みる
# WP REST API 経由でテーマをインストールするエンドポイント:
# POST /wp-json/wp/v2/themes (WP 5.5+)
# ----------------------------
print("=== WordPress REST API テーマアップロード ===")

auth = HTTPBasicAuth(WP_USERNAME, WP_APP_PASSWORD)

# まず接続確認
try:
    resp = requests.get(
        f"{WP_URL}/wp-json/wp/v2/users/me",
        auth=auth,
        verify=SSL_VERIFY,
        timeout=30
    )
    print(f"認証確認: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print(f"ログイン済みユーザー: {data.get('name', 'unknown')}")
        print(f"権限: {data.get('roles', [])}")
    else:
        print(f"認証失敗: {resp.text[:200]}")
except Exception as e:
    print(f"接続エラー: {e}")

print()

# WP REST API でテーマアップロード（プラグイン/テーマのアップロードはWP 5.5+で対応）
print("=== テーマZIPをREST API経由でアップロード ===")
try:
    with open(ZIP_PATH, 'rb') as f:
        zip_data = f.read()

    resp = requests.post(
        f"{WP_URL}/wp-json/wp/v2/themes",
        auth=auth,
        verify=SSL_VERIFY,
        timeout=60,
        files={
            'file': ('premier-blog-theme.zip', zip_data, 'application/zip')
        }
    )
    print(f"レスポンス: {resp.status_code}")
    print(f"内容: {resp.text[:500]}")
except Exception as e:
    print(f"アップロードエラー: {e}")

print()

# ----------------------------
# ステップ3: WP Admin 経由でアップロード（nonce取得→POSTの流れ）
# ----------------------------
print("=== WP Admin 経由アップロード ===")

session = requests.Session()
session.auth = (WP_USERNAME, WP_APP_PASSWORD)
session.verify = SSL_VERIFY

# ログインページを取得
login_url = f"{WP_URL}/wp-login.php"
try:
    resp = session.get(login_url, timeout=30)
    print(f"ログインページ取得: {resp.status_code}")
except Exception as e:
    print(f"ログインページ取得エラー: {e}")
    sys.exit(1)

# WP ログイン
login_data = {
    'log': WP_USERNAME,
    'pwd': WP_APP_PASSWORD.replace(' ', ''),  # スペースを除去
    'wp-submit': 'Log In',
    'redirect_to': '/wp-admin/',
    'testcookie': '1',
}
try:
    resp = session.post(login_url, data=login_data, timeout=30, allow_redirects=True)
    print(f"ログイン試行: {resp.status_code}, URL={resp.url}")

    # ログイン成功確認
    if 'wp-admin' in resp.url or resp.status_code == 200:
        # themes upload ページを取得してnonce取得
        upload_page_url = f"{WP_URL}/wp-admin/theme-install.php?upload"
        resp2 = session.get(upload_page_url, timeout=30)
        print(f"テーマアップロードページ取得: {resp2.status_code}")

        # nonce を探す
        import re
        nonce_match = re.search(r'name="_wpnonce"\s+value="([^"]+)"', resp2.text)
        if not nonce_match:
            nonce_match = re.search(r'"_wpnonce"\s*:\s*"([^"]+)"', resp2.text)
        if not nonce_match:
            nonce_match = re.search(r'themeupload_nonce\s*=\s*[\'"]([^\'"]+)[\'"]', resp2.text)

        if nonce_match:
            nonce = nonce_match.group(1)
            print(f"Nonce取得: {nonce}")

            # テーマアップロード
            with open(ZIP_PATH, 'rb') as f:
                zip_data = f.read()

            upload_resp = session.post(
                f"{WP_URL}/wp-admin/update.php?action=upload-theme",
                data={
                    '_wpnonce': nonce,
                    '_wp_http_referer': '/wp-admin/theme-install.php',
                    'install-button': 'Install Now',
                },
                files={
                    'themezip': ('premier-blog-theme.zip', zip_data, 'application/zip')
                },
                timeout=120
            )
            print(f"アップロード結果: {upload_resp.status_code}")
            # 成功/失敗を確認
            if 'Theme installed successfully' in upload_resp.text or 'テーマをインストールしました' in upload_resp.text:
                print("✓ テーマアップロード成功！")
            elif 'theme_already_exists' in upload_resp.text or 'already exists' in upload_resp.text.lower():
                print("⚠ テーマは既に存在（上書きには別途対応が必要）")
                # 上書き確認ページの場合、overwriteパラメータでPOST
                overwrite_match = re.search(r'action="([^"]*update\.php[^"]*)"', upload_resp.text)
                overwrite_nonce = re.search(r'name="_wpnonce"\s+value="([^"]+)"', upload_resp.text)
                if overwrite_match and overwrite_nonce:
                    print("上書きを試みる...")
                    with open(ZIP_PATH, 'rb') as f:
                        zip_data = f.read()
                    ow_resp = session.post(
                        WP_URL + overwrite_match.group(1),
                        data={
                            '_wpnonce': overwrite_nonce.group(1),
                            'overwrite': 'update-theme',
                            'theme': 'premier-blog-theme',
                        },
                        files={
                            'themezip': ('premier-blog-theme.zip', zip_data, 'application/zip')
                        },
                        timeout=120
                    )
                    print(f"上書き結果: {ow_resp.status_code}")
                    print(f"レスポンス（先頭500文字）: {ow_resp.text[:500]}")
            else:
                print(f"レスポンス（先頭800文字）:\n{upload_resp.text[:800]}")
        else:
            print("Nonceが見つからなかった")
            # デバッグ用にページの一部を表示
            print(f"ページ内容（先頭1000文字）:\n{resp2.text[:1000]}")
    else:
        print(f"ログイン失敗。レスポンス: {resp.text[:300]}")

except Exception as e:
    import traceback
    print(f"エラー: {e}")
    traceback.print_exc()

print()
print("=== 完了 ===")
