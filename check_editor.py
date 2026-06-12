#!/usr/bin/env python3
import os, sys, io, re, requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

load_dotenv('C:/premier-blog/.env')
WP_URL = os.getenv('WP_URL', '').rstrip('/')
WP_USERNAME = os.getenv('WP_USERNAME', '')
WP_APP_PASSWORD = os.getenv('WP_APP_PASSWORD', '')
SSL_VERIFY = os.getenv('SSL_VERIFY', 'true').lower() != 'false'

auth = HTTPBasicAuth(WP_USERNAME, WP_APP_PASSWORD)

resp = requests.get(
    f"{WP_URL}/wp-admin/theme-editor.php",
    auth=auth,
    verify=SSL_VERIFY,
    timeout=30,
    params={'file': 'front-page.php', 'theme': 'premier-blog-theme'},
)

print(f"Status: {resp.status_code}")
print(f"URL: {resp.url}")
print(f"Has loginform: {'loginform' in resp.text}")
print(f"Has theme-editor: {'theme-editor' in resp.text}")
print(f"Has newcontent: {'newcontent' in resp.text}")
print()

# nonceを探す（より広範に）
nonce_candidates = re.findall(r'[a-f0-9]{10}', resp.text)
print(f"10桁16進数候補: {set(nonce_candidates)}")

# フォームを探す
forms = re.findall(r'<form[^>]*name="[^"]*"[^>]*>', resp.text)
print(f"Named forms: {forms}")

# inputタグを探す
inputs = re.findall(r'<input[^>]*name="(?:nonce|_wpnonce)[^>]*>', resp.text, re.IGNORECASE)
print(f"Nonce inputs: {inputs}")

# ページの先頭500文字
print(f"\nページ先頭500文字:\n{resp.text[:500]}")
print(f"\nページ末尾500文字:\n{resp.text[-500:]}")
