"""
style.css を WordPress REST API経由でデプロイするスクリプト。
事前に functions.php に /wp-json/premier-blog/v1/update-css エンドポイントが必要。
"""
import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv("C:/premier-blog/.env")
WP_URL = os.getenv("WP_URL", "").rstrip("/")
auth = HTTPBasicAuth(os.getenv("WP_USERNAME", ""), os.getenv("WP_APP_PASSWORD", ""))

with open("wordpress/Premier-blog/premier-blog-theme/style.css", encoding="utf-8") as f:
    css_content = f.read()

print(f"style.css: {len(css_content):,} bytes")

resp = requests.post(
    f"{WP_URL}/wp-json/premier-blog/v1/update-css",
    auth=auth,
    verify=False,
    json={"css": css_content},
    timeout=60,
)

print(f"status: {resp.status_code}")
print(resp.text[:300])
