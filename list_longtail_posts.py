"""
ピラー記事候補の洗い出し用ワンショットスクリプト。
config.yaml: topic_longtail.groups のお題文字列を processed.db と突合し、
既に生成済みのロングテール記事の投稿ID・URLを一覧表示する。
internal_links.pillar_posts に採用するIDをユーザーが選び、config.yaml へ手動反映する想定。
"""

import sqlite3

import yaml

CONFIG_PATH = "config.yaml"
DB_PATH = "processed.db"


def main() -> None:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    groups = config.get("topic_longtail", {}).get("groups", [])
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    for group in groups:
        category = group.get("category", "?")
        print(f"\n=== category: {category} ===")
        for theme in group.get("themes", []):
            cur.execute(
                "SELECT wp_post_id, wp_url, created_at FROM processed_topics WHERE topic_title = ?",
                (theme,),
            )
            row = cur.fetchone()
            if row and row[0]:
                post_id, wp_url, created_at = row
                print(f"  [post_id={post_id}] {theme}  ({wp_url}, {created_at})")
            else:
                print(f"  [未生成]      {theme}")

    conn.close()


if __name__ == "__main__":
    main()
