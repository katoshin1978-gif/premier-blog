"""
audit_thin_articles.py のCSVを確認・編集した後、ユーザーが承認した投稿IDにだけ
noindexを適用するスクリプト。functions.php の /premier-blog/v1/set-noindex エンドポイントを叩く。

使い方:
    python apply_noindex.py 1234 1235 1240      # 個別ID指定
    python apply_noindex.py --undo 1234          # noindex解除
"""

import argparse
import os
import sys

import requests

import publisher as pub

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("post_ids", nargs="+", type=int)
    parser.add_argument("--undo", action="store_true", help="指定IDのnoindexを解除する")
    args = parser.parse_args()

    config = pub.load_config()
    wp_cfg = config.get("wordpress", {})
    base_url = os.environ.get("WP_URL", wp_cfg.get("url", "")).rstrip("/")
    ip_base_url, host_header = pub._resolve_to_ip(base_url)

    for post_id in args.post_ids:
        try:
            resp = requests.post(
                f"{ip_base_url}/wp-json/premier-blog/v1/set-noindex",
                json={"post_id": post_id, "noindex": not args.undo},
                headers={**pub._get_auth_header(), **host_header, "Content-Type": "application/json"},
                timeout=15,
                verify=pub._SSL_VERIFY,
            )
            resp.raise_for_status()
            action = "解除" if args.undo else "適用"
            print(f"[apply_noindex] post {post_id}: noindex{action}完了")
        except Exception as e:
            print(f"[apply_noindex] post {post_id}: 失敗 ({e})")


if __name__ == "__main__":
    main()
