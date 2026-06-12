#!/usr/bin/env python3
"""カスタムAPIエンドポイントの詳細確認と利用"""
import os, sys, io, re, requests, zipfile, json
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

load_dotenv('C:/premier-blog/.env')
WP_URL = os.getenv('WP_URL', '').rstrip('/')
WP_USERNAME = os.getenv('WP_USERNAME', '')
WP_APP_PASSWORD = os.getenv('WP_APP_PASSWORD', '')
SSL_VERIFY = os.getenv('SSL_VERIFY', 'true').lower() != 'false'

auth = HTTPBasicAuth(WP_USERNAME, WP_APP_PASSWORD)

print("=== /premier-blog/v1/ エンドポイント確認 ===")
endpoints = [
    '/premier-blog/v1/ticker',
    '/premier-blog/v1/league-table',
    '/premier-blog/v1/acf-options',
    '/premier-blog/v1/multi-league',
    '/premier-blog/v1/set-option',
]

for ep in endpoints:
    resp = requests.get(
        f"{WP_URL}/wp-json{ep}",
        auth=auth,
        verify=SSL_VERIFY,
        timeout=10,
    )
    print(f"  GET {ep}: {resp.status_code} - {resp.text[:100]}")

print()
print("=== set-option POST テスト ===")
resp = requests.post(
    f"{WP_URL}/wp-json/premier-blog/v1/set-option",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
    json={'key': 'test_key', 'value': 'test_value'}
)
print(f"POST set-option: {resp.status_code} - {resp.text[:200]}")

print()
print("=== ルート詳細確認 ===")
routes_resp = requests.get(
    f"{WP_URL}/wp-json/",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
)
if routes_resp.status_code == 200:
    routes = routes_resp.json().get('routes', {})
    for route_path, route_data in routes.items():
        if 'premier-blog' in route_path:
            print(f"Route: {route_path}")
            for endpoint in route_data.get('endpoints', []):
                print(f"  Methods: {endpoint.get('methods', [])}")
                print(f"  Args: {list(endpoint.get('args', {}).keys())}")
