"""
AdSense publisher ID をサイトに設定するスクリプト。
テーマ更新後に実行。
Usage: python set_adsense.py ca-pub-XXXXXXXXXXXXXXXX
"""
import os, sys, base64, requests
from dotenv import load_dotenv
load_dotenv()

def main():
    if len(sys.argv) < 2:
        print("Usage: python set_adsense.py ca-pub-XXXXXXXXXXXXXXXX")
        sys.exit(1)

    publisher_id = sys.argv[1].strip()
    if not publisher_id.startswith("ca-pub-"):
        print("エラー: IDは ca-pub- で始まる形式です")
        sys.exit(1)

    ssl = os.environ.get("SSL_VERIFY", "true").lower() != "false"
    token = base64.b64encode(
        f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()
    ).decode()
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    resp = requests.post(
        "https://premier-blog.com/wp-json/premier-blog/v1/set-option",
        json={"key": "adsense_publisher_id", "value": publisher_id},
        headers=headers,
        verify=ssl,
        timeout=15,
    )
    if resp.ok:
        print(f"AdSense ID を設定しました: {publisher_id}")
        print("全ページの <head> に自動でコードが挿入されます。")
    else:
        print(f"設定失敗: {resp.status_code} {resp.text[:200]}")

if __name__ == "__main__":
    main()
