"""
既存の公開済み記事の「関連記事」ブロックを最新の内部リンクロジック（ピラー記事＋新着＋
カテゴリアーカイブリンク）で再生成し、本文を上書きするバックフィルスクリプト。

使い方:
    python backfill_internal_links.py --dry-run --limit 5   # 差分確認のみ
    python backfill_internal_links.py --limit 50             # 先頭50件だけ実際に更新
    python backfill_internal_links.py --start-id 2000         # 投稿ID2000以降のみ対象
"""

import argparse
import os
import re
import sys
import time

import requests

import publisher as pub

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_RELATED_RE = re.compile(r'<div class="related-posts">.*?</div>', re.DOTALL)


def build_new_related_block(config: dict, base_url: str, ip_base_url: str, host_header: dict, post: dict) -> str | None:
    post_cats: list[int] = post.get("categories", [])
    if not post_cats:
        return None

    wp_cfg = config.get("wordpress", {})
    category_ids_map = wp_cfg.get("category_ids", {})
    id_to_slug = {v: k for k, v in category_ids_map.items()}

    related = pub._fetch_related_posts(base_url, ip_base_url, host_header, post_cats, post["id"], count=4)

    pillar_map = {int(k): v for k, v in config.get("internal_links", {}).get("pillar_posts", {}).items()}
    pillar = pub._fetch_pillar_post(ip_base_url, host_header, post_cats, post["id"], pillar_map) if pillar_map else None
    if pillar:
        related = [p for p in related if p["id"] != pillar["id"]]
        related = [pillar] + related[:3]

    primary_slug = id_to_slug.get(post_cats[0], "")
    category_archive_url = f"{base_url}/category/{primary_slug}/" if primary_slug else ""
    category_label = pub._CATEGORY_LABELS.get(primary_slug, "")
    data_room_url = f"{base_url}/data-room/"

    return pub._build_related_html(related, data_room_url, category_archive_url, category_label)


def replace_related_block(content_html: str, new_block: str) -> str:
    if _RELATED_RE.search(content_html):
        return _RELATED_RE.sub(new_block.replace("\\", "\\\\"), content_html, count=1)
    return content_html + "\n" + new_block


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-id", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.5, help="更新間のスリープ秒数（サーバー負荷対策）")
    args = parser.parse_args()

    config = pub.load_config()
    wp_cfg = config.get("wordpress", {})
    base_url = os.environ.get("WP_URL", wp_cfg.get("url", "")).rstrip("/")
    ip_base_url, host_header = pub._resolve_to_ip(base_url)

    print("[backfill] 全公開記事を取得中...")
    posts = pub.fetch_all_published_posts(base_url, ip_base_url, host_header)
    posts = [p for p in posts if p["id"] >= args.start_id]
    if args.limit:
        posts = posts[: args.limit]
    print(f"[backfill] 対象記事数: {len(posts)}")

    updated_count = 0
    skipped_count = 0
    for post in posts:
        new_block = build_new_related_block(config, base_url, ip_base_url, host_header, post)
        if not new_block:
            skipped_count += 1
            continue

        old_content = post["content"]["raw"]
        updated_content = replace_related_block(old_content, new_block)
        if updated_content == old_content:
            skipped_count += 1
            continue

        if args.dry_run:
            print(f"\n--- post {post['id']}: {post['title']['rendered']} ---")
            print(new_block)
        else:
            try:
                requests.post(
                    f"{ip_base_url}/wp-json/wp/v2/posts/{post['id']}",
                    json={"content": updated_content},
                    headers={**pub._get_auth_header(), **host_header, "Content-Type": "application/json"},
                    timeout=15,
                    verify=pub._SSL_VERIFY,
                )
                print(f"[backfill] post {post['id']} 更新完了")
                updated_count += 1
                time.sleep(args.sleep)
            except Exception as e:
                print(f"[backfill] post {post['id']} 更新失敗: {e}")

    if args.dry_run:
        print(f"\n[backfill] dry-run完了: 更新対象 {len(posts) - skipped_count}件 / スキップ {skipped_count}件")
    else:
        print(f"\n[backfill] 完了: 更新 {updated_count}件 / スキップ {skipped_count}件")


if __name__ == "__main__":
    main()
