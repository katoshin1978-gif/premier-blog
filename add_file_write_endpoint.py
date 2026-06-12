#!/usr/bin/env python3
"""
functions.php にファイル書き込みエンドポイントを追加してzipを再作成する。
このエンドポイント経由でテーマファイルを更新する。

フロー:
1. zipからfunctions.phpを読み込み
2. ファイル書き込みエンドポイントを追記
3. 新しいzipを作成
4. そのzipをどうやってWordPressに入れるか...（これが問題）

実は最終的にはブラウザからの手動アップロードが必要。
ただし、まず「functions.phpに更新エンドポイントを含むzip」を作成しておく。
そのzipを管理画面からアップロード後、エンドポイント経由で残りのファイルを更新できる。
"""
import os, sys, io, zipfile, json
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

load_dotenv('C:/premier-blog/.env')
WP_URL = os.getenv('WP_URL', '').rstrip('/')
WP_USERNAME = os.getenv('WP_USERNAME', '')
WP_APP_PASSWORD = os.getenv('WP_APP_PASSWORD', '')
SSL_VERIFY = os.getenv('SSL_VERIFY', 'true').lower() != 'false'

ZIP_PATH = 'C:/premier-blog/premier-blog-theme.zip'

# =====================================================
# ステップ1: 現在のzipからfunctions.phpを読み込み
# =====================================================
print("=== functions.phpにファイル書き込みエンドポイントを追加 ===")

with zipfile.ZipFile(ZIP_PATH, 'r') as z:
    functions_content = z.read('functions.php').decode('utf-8')

# 追加するエンドポイント（テーマファイルを書き込む）
FILE_WRITE_ENDPOINT = '''

/* ===== テーマファイル更新エンドポイント（一時的）===== */
add_action("rest_api_init", function () {
    register_rest_route("premier-blog/v1", "/update-file", [
        "methods"             => "POST",
        "callback"            => function (WP_REST_Request $request) {
            $file    = $request->get_param("file");
            $content = $request->get_param("content");
            if (!$file || $content === null) {
                return new WP_REST_Response(["error" => "file and content required"], 400);
            }
            // パストラバーサル対策
            $file = str_replace(['..', '\\\\'], '', $file);
            $theme_dir = get_template_directory();
            $full_path = $theme_dir . '/' . ltrim($file, '/');
            // テーマディレクトリ内のみ許可
            if (strpos(realpath(dirname($full_path)), realpath($theme_dir)) !== 0) {
                return new WP_REST_Response(["error" => "invalid path"], 403);
            }
            // ディレクトリ作成
            $dir = dirname($full_path);
            if (!file_exists($dir)) {
                mkdir($dir, 0755, true);
            }
            $result = file_put_contents($full_path, $content);
            if ($result === false) {
                return new WP_REST_Response(["error" => "write failed"], 500);
            }
            return new WP_REST_Response(["ok" => true, "file" => $file, "bytes" => $result], 200);
        },
        "permission_callback" => function () {
            return current_user_can("manage_options");
        },
    ]);
});
'''

# functions.phpの末尾に追加
if 'update-file' not in functions_content:
    functions_content_new = functions_content.rstrip() + '\n' + FILE_WRITE_ENDPOINT + '\n'
    print("ファイル書き込みエンドポイントを追加した")
else:
    functions_content_new = functions_content
    print("ファイル書き込みエンドポイントは既に存在する")

# =====================================================
# ステップ2: 新しいzipを作成
# =====================================================
print("\n新しいzipを作成中...")

zip_buffer = io.BytesIO()
with zipfile.ZipFile(ZIP_PATH, 'r') as zin:
    with zipfile.ZipFile(zip_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.namelist():
            if item == 'functions.php':
                # 更新されたfunctions.phpを使う
                zout.writestr(item, functions_content_new.encode('utf-8'))
                print(f"  更新: {item}")
            else:
                data = zin.read(item)
                zout.writestr(item, data)
                print(f"  コピー: {item}")

zip_data = zip_buffer.getvalue()
print(f"新しいzipサイズ: {len(zip_data)} bytes")

# 元のzipを上書き保存
with open(ZIP_PATH, 'wb') as f:
    f.write(zip_data)
print(f"zip保存完了: {ZIP_PATH}")

# =====================================================
# ステップ3: このzipをWordPressにアップロードする試み
# WordPress REST API POST /wp/v2/themes はこのサーバーでは動かない
# WP Admin Basic Authも動かない
# なので、update-file エンドポイントが既に存在するか確認し、
# 存在すれば直接ファイル更新、なければ手動アップロードが必要
# =====================================================
print("\n=== 既存の update-file エンドポイント確認 ===")

auth = HTTPBasicAuth(WP_USERNAME, WP_APP_PASSWORD)

test_resp = requests.post(
    f"{WP_URL}/wp-json/premier-blog/v1/update-file",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
    json={
        'file': 'test-check.txt',
        'content': 'test'
    }
)
print(f"ステータス: {test_resp.status_code}")
print(f"レスポンス: {test_resp.text[:200]}")

if test_resp.status_code == 200:
    print("\nupdate-file エンドポイントが使える! ファイル更新を実行する...")

    TARGET_FILES = [
        'front-page.php',
        'template-parts/hero-asym.php',
        'template-parts/card.php',
        'template-parts/card-row.php',
    ]

    with zipfile.ZipFile(ZIP_PATH, 'r') as z:
        for target in TARGET_FILES:
            content = z.read(target).decode('utf-8')
            resp = requests.post(
                f"{WP_URL}/wp-json/premier-blog/v1/update-file",
                auth=auth,
                verify=SSL_VERIFY,
                timeout=60,
                json={'file': target, 'content': content}
            )
            print(f"  {target}: {resp.status_code} - {resp.text[:100]}")
else:
    print("\nupdate-file エンドポイントはまだ存在しない（サーバー上の古いfunctions.phpを使用中）")
    print("手順:")
    print(f"1. ブラウザで {WP_URL}/wp-admin/theme-install.php?upload を開く")
    print(f"2. {ZIP_PATH} をアップロードする（上書きインストール）")
    print("3. アップロード後、update-file エンドポイントが利用可能になる")
    print("4. その後このスクリプトを再実行すれば残りのファイルも更新される")
