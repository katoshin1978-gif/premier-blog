#!/usr/bin/env python3
"""
プラグインとしてZIPをアップロードし、テーマファイル更新エンドポイントを注入する
プラグインZIPは /wp/v2/plugins 経由でインストールできる（WP 5.5+）
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

auth = HTTPBasicAuth(WP_USERNAME, WP_APP_PASSWORD)

# =====================================================
# ステップ1: テーマ更新用の一時プラグインを作成
# =====================================================
print("=== テーマファイル更新用プラグインZIPを作成 ===")

# テーマファイルを読み込む
THEME_ZIP = 'C:/premier-blog/premier-blog-theme.zip'
theme_files = {}
with zipfile.ZipFile(THEME_ZIP, 'r') as z:
    for name in z.namelist():
        theme_files[name] = z.read(name).decode('utf-8')

# プラグインのPHPコード（テーマファイルを更新するエンドポイント）
plugin_php = '''<?php
/**
 * Plugin Name: PL Theme Updater
 * Plugin URI: https://premier-blog.com
 * Description: テーマファイルを更新するための一時的プラグイン
 * Version: 1.0.0
 * Author: Premier Blog
 */

if (!defined('ABSPATH')) exit;

/* ===== テーマファイル更新エンドポイント ===== */
add_action('rest_api_init', function () {
    register_rest_route('premier-blog/v1', '/update-theme-file', [
        'methods'             => 'POST',
        'callback'            => function (WP_REST_Request $request) {
            $file    = $request->get_param('file');
            $content = $request->get_param('content');
            if (!$file || $content === null) {
                return new WP_REST_Response(['error' => 'file and content required'], 400);
            }
            // パストラバーサル対策
            $file = str_replace(['..', '\\\\'], '', $file);
            $theme_dir = get_template_directory();
            $full_path = $theme_dir . '/' . ltrim($file, '/');
            // テーマディレクトリ内のみ許可
            $real_theme = realpath($theme_dir);
            $real_parent = realpath(dirname($full_path));
            if ($real_parent === false || strpos($real_parent, $real_theme) !== 0) {
                return new WP_REST_Response(['error' => 'invalid path'], 403);
            }
            // ディレクトリ作成
            $dir = dirname($full_path);
            if (!file_exists($dir)) {
                wp_mkdir_p($dir);
            }
            $result = file_put_contents($full_path, $content);
            if ($result === false) {
                return new WP_REST_Response(['error' => 'write failed', 'path' => $full_path], 500);
            }
            return new WP_REST_Response(['ok' => true, 'file' => $file, 'bytes' => $result], 200);
        },
        'permission_callback' => function () {
            return current_user_can('manage_options');
        },
    ]);
});

/* ===== 自己削除エンドポイント ===== */
add_action('rest_api_init', function () {
    register_rest_route('premier-blog/v1', '/remove-updater', [
        'methods'             => 'POST',
        'callback'            => function (WP_REST_Request $request) {
            $plugin_file = plugin_dir_path(__FILE__) . 'pl-theme-updater.php';
            deactivate_plugins('pl-theme-updater/pl-theme-updater.php');
            if (function_exists('delete_plugins')) {
                delete_plugins(['pl-theme-updater/pl-theme-updater.php']);
            }
            return new WP_REST_Response(['ok' => true, 'message' => 'Plugin removed'], 200);
        },
        'permission_callback' => function () {
            return current_user_can('manage_options');
        },
    ]);
});
'''

# プラグインZIPを作成
plugin_zip_buffer = io.BytesIO()
with zipfile.ZipFile(plugin_zip_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
    zout.writestr('pl-theme-updater/pl-theme-updater.php', plugin_php)

plugin_zip_data = plugin_zip_buffer.getvalue()
print(f"プラグインZIPサイズ: {len(plugin_zip_data)} bytes")

# =====================================================
# ステップ2: プラグインをインストール（REST API POST /wp/v2/plugins）
# =====================================================
print("\n=== プラグインをREST API経由でインストール ===")

# 方法1: multipart
print("-- 方法1: multipart/form-data --")
resp = requests.post(
    f"{WP_URL}/wp-json/wp/v2/plugins",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=120,
    files={
        'file': ('pl-theme-updater.zip', plugin_zip_data, 'application/zip')
    }
)
print(f"ステータス: {resp.status_code}")
print(f"レスポンス: {resp.text[:500]}")

if resp.status_code in [200, 201]:
    print("プラグインインストール成功!")
    plugin_data = resp.json()
    plugin_slug = plugin_data.get('plugin', 'pl-theme-updater/pl-theme-updater')
    print(f"プラグインスラッグ: {plugin_slug}")

    # プラグインを有効化
    print("\n=== プラグインを有効化 ===")
    activate_resp = requests.post(
        f"{WP_URL}/wp-json/wp/v2/plugins/{plugin_slug}",
        auth=auth,
        verify=SSL_VERIFY,
        timeout=60,
        json={'status': 'active'}
    )
    print(f"有効化: {activate_resp.status_code} - {activate_resp.text[:200]}")

else:
    # 方法2: raw binary
    print("\n-- 方法2: raw binary --")
    resp2 = requests.post(
        f"{WP_URL}/wp-json/wp/v2/plugins",
        auth=auth,
        verify=SSL_VERIFY,
        timeout=120,
        headers={
            'Content-Type': 'application/zip',
            'Content-Disposition': 'attachment; filename="pl-theme-updater.zip"',
        },
        data=plugin_zip_data
    )
    print(f"ステータス: {resp2.status_code}")
    print(f"レスポンス: {resp2.text[:500]}")

    if resp2.status_code in [200, 201]:
        print("プラグインインストール成功!")
    else:
        print("\nプラグインAPIも動作しない")
        print("WordPress管理画面からの手動アップロードが必要")

# =====================================================
# update-theme-file エンドポイントが使えるか確認
# =====================================================
print("\n=== update-theme-file エンドポイント確認 ===")
check_resp = requests.get(
    f"{WP_URL}/wp-json/premier-blog/v1/update-theme-file",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=10,
)
print(f"ステータス: {check_resp.status_code}")
if check_resp.status_code != 404:
    print("エンドポイントが存在する! テーマファイルを更新する...")

    TARGET_FILES = [
        'front-page.php',
        'template-parts/hero-asym.php',
        'template-parts/card.php',
        'template-parts/card-row.php',
    ]

    with zipfile.ZipFile(THEME_ZIP, 'r') as z:
        for target in TARGET_FILES:
            content = z.read(target).decode('utf-8')
            resp = requests.post(
                f"{WP_URL}/wp-json/premier-blog/v1/update-theme-file",
                auth=auth,
                verify=SSL_VERIFY,
                timeout=60,
                json={'file': target, 'content': content}
            )
            print(f"  {target}: {resp.status_code} - {resp.text[:100]}")
