"""
薄い記事の棚卸しレポート生成スクリプト。
全公開記事の本文からHTMLタグ・自動挿入ボイラープレート（関連記事・アフィリエイトカード）を
除去した実質文字数を集計し、少ない順にCSV出力する。自動でのnoindex適用は一切行わない
（レポートのみ。適用は apply_noindex.py で承認済みIDに対して個別に行う）。

使い方:
    python audit_thin_articles.py --out thin_articles.csv
"""

import argparse
import csv
import os
import re
import sys

import publisher as pub

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_BOILERPLATE_RE = re.compile(
    r'<div class="related-posts">.*?</div>'
    r'|<div class="affiliate-block".*?</div>\s*</div>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_THIN_THRESHOLD = 800


def core_char_count(raw_html: str) -> int:
    stripped = _BOILERPLATE_RE.sub("", raw_html)
    text = _TAG_RE.sub("", stripped)
    text = re.sub(r"\s+", "", text)
    return len(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="thin_articles.csv")
    args = parser.parse_args()

    config = pub.load_config()
    wp_cfg = config.get("wordpress", {})
    base_url = os.environ.get("WP_URL", wp_cfg.get("url", "")).rstrip("/")
    ip_base_url, host_header = pub._resolve_to_ip(base_url)
    id_to_slug = {v: k for k, v in wp_cfg.get("category_ids", {}).items()}

    print("[audit] 全公開記事を取得中...")
    posts = pub.fetch_all_published_posts(base_url, ip_base_url, host_header)
    print(f"[audit] 対象記事数: {len(posts)}")

    rows = []
    for post in posts:
        raw = post["content"].get("raw", post["content"].get("rendered", ""))
        char_count = core_char_count(raw)
        cats = post.get("categories", [])
        category = id_to_slug.get(cats[0], "") if cats else ""
        rows.append({
            "post_id": post["id"],
            "title": post["title"]["rendered"],
            "category": category,
            "char_count": char_count,
            "url": post.get("link", ""),
            "published_date": post.get("date", ""),
            "candidate": "YES" if char_count < _THIN_THRESHOLD else "",
        })

    rows.sort(key=lambda r: r["char_count"])

    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "post_id", "title", "category", "char_count", "url", "published_date", "candidate",
        ])
        writer.writeheader()
        writer.writerows(rows)

    candidates = [r for r in rows if r["candidate"] == "YES"]
    print(f"[audit] CSV出力完了: {args.out}")
    print(f"[audit] 全{len(rows)}件中、{_THIN_THRESHOLD}文字未満の候補: {len(candidates)}件")


if __name__ == "__main__":
    main()
