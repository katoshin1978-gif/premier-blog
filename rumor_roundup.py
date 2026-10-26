"""
クラブ別の移籍噂まとめ記事モジュール
クラブごとに1本の記事を持ち、update_interval_days ごとに最新の噂で同じ記事を書き直す。
タイトル・スラッグは固定し、「アーセナル 移籍噂」等の検索評価を1つのURLに集める。
"""

import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta

import anthropic
import httpx

from fetcher import fetch_articles
from image_fetcher import fetch_image
from publisher import fetch_post_for_update, html_to_markdown_for_update, publish_draft, upload_media
from researcher import search_articles
from synthesizer import (
    MODEL,
    PLAYER_NAMES_GUIDE,
    _SSL_VERIFY,
    GeneratedArticle,
    _extract_seo_meta,
    _normalize_player_names,
    _remove_meta_comments,
    build_club_facts_text,
    build_source_context,
    is_too_short,
    load_config,
)

# 噂まとめに使う最低ソース数（1媒体だけの噂一覧にしない）
MIN_SOURCES = 3

RUMOR_SYSTEM_PROMPT = """あなたはプレミアリーグ専門の日本語スポーツライターです。
特定クラブの移籍の噂を、確度と出元が一目で分かる「噂まとめ」記事として整理します。

【絶対に守るルール】
- 提供されたソース（と既存記事）の情報のみを使用する（知識の補完は禁止）
- ソースに明示されていない移籍金・契約期間・所属クラブ・ポジションを推測で書かない。分からない項目は省く
- 確度はソースの表現に合わせる。official / confirmed / here we go / signs / completes move 等がある場合のみ「決定」「合意」。
  in talks / negotiations / bid は「交渉中」、interested / monitoring / target / linked は「関心」、
  出元が1媒体だけの伝聞（reportedly / claims / could）は「噂」。ソースより確度を高く書くのは重大な違反である
- 噂の出元（報じた媒体名）を必ず書く。ソースが他紙の報道を紹介している場合（Sky Sports の Paper Talk、BBC の Gossip 等）は、
  元の媒体名（The Sun、Daily Mail 等）を出元として書く
- 日付は「M月D日」形式で、ソースの「Published:」の日付か既存記事に書かれた日付だけを使う。推測で日付を付けない
- 記事中でのソース引用は1ソースあたり最大100語（英語換算）まで。残りは自分の言葉で要約・言い換えする
- 著作権を尊重し、原文の長文コピーは行わない
- ソースの量・質・状態についてのメタコメントを記事本文に書かない
- 記事末尾の「情報源」セクションで、使ったソースをすべてリスト化する

【記事の書き方】
- 噂ごとに見出しを立て、確度の高い順に並べる
- 各噂には、なぜそのクラブがその選手を必要としているのか（または放出したいのか）を1〜3文で添える
- 同じ選手の噂は1つにまとめ、複数の媒体が報じていれば出元を併記する

【文字数の目標】
- 記事全体：2000〜4000文字

""" + PLAYER_NAMES_GUIDE + """
【出力フォーマット（厳守）】
```
# {タイトル}

## はじめに
**【{M月D日}更新】** {今回の更新で動きのあった噂の要点を1文で}
{このクラブの補強の焦点・移籍市場の状況を3〜4行で}

## 獲得の噂
### {選手名}（{現所属}・{ポジション}）
- **確度**: {決定 / 合意 / 交渉中 / 関心 / 噂}
- **主な報道**: {媒体名}（{M月D日}）、{媒体名}（{M月D日}）
{解説1〜3文。出典インライン例：([Sky Sports](URL))}

## 放出の噂
### {選手名}（{ポジション}）
- **確度**: {同上}
- **主な報道**: {媒体名}（{M月D日}）
{解説}

## 決着・立ち消えになった噂
- **{選手名}**：{結末}（{M月D日}）

## 編集部の見解
{補強戦略全体への評価。どの噂が最も現実的か、どこに穴が残るかを断言調で300文字以上}

## 今後の注目点
{移籍市場の期限・交渉の山場など、次に何を見ればいいか。200文字以上}

---
**情報源**
- [{記事タイトル}]({URL}) - {サイト名}
```
該当する噂がない節（放出の噂・決着した噂）は見出しごと省いてよい。"""


def init_rumor_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rumor_posts (
            club_key TEXT PRIMARY KEY,
            wp_post_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _get_rumor_post(conn: sqlite3.Connection, club_key: str) -> tuple[int, str] | None:
    row = conn.execute(
        "SELECT wp_post_id, updated_at FROM rumor_posts WHERE club_key=?", (club_key,)
    ).fetchone()
    return (row[0], row[1]) if row else None


def _save_rumor_post(conn: sqlite3.Connection, club_key: str, post_id: int) -> None:
    conn.execute(
        "INSERT INTO rumor_posts (club_key, wp_post_id, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(club_key) DO UPDATE SET wp_post_id=excluded.wp_post_id, updated_at=excluded.updated_at",
        (club_key, post_id, datetime.now().isoformat()),
    )
    conn.commit()


def _collect_sources(club: dict, config_path: str) -> tuple[list, list]:
    """噂系の検索語を複数回投げてソースを集める（同一URLは除外）"""
    results = []
    seen: set[str] = set()
    for q in club["queries"]:
        for r in search_articles(q, config_path, context="transfers"):
            if r.url not in seen:
                seen.add(r.url)
                results.append(r)
    articles = fetch_articles([r.url for r in results]) if results else []
    return results, articles


def generate_rumor_article(
    club: dict,
    articles: list,
    search_results: list,
    existing_markdown: str | None,
    config_path: str = "config.yaml",
) -> GeneratedArticle:
    """噂まとめ記事を生成する。既存記事があれば最新ソースで書き直す。
    新しい動きがなければ content="SKIP_NO_NEW_FACTS" を返す"""
    config = load_config(config_path)
    max_quote_words = config["search"].get("max_quote_words", 100)
    max_context_words = config["search"].get("max_context_words", 500)
    source_context = build_source_context(articles, search_results, max_context_words, max_quote_words)
    today = date.today()

    if existing_markdown:
        task = (
            "既に公開している噂まとめ記事を、新しいソースで最新の状態に書き直してください。\n"
            "- 新しいソースに出てきた噂を追加し、既存の噂は確度・報道を最新に更新する\n"
            "- 既存記事の噂は、新しいソースで否定されていない限り残してよい。ただし「主な報道」の最新の日付が"
            "今日から30日以上前の噂は「決着・立ち消えになった噂」に移す\n"
            "- 移籍が決定した・破談になった噂は「決着・立ち消えになった噂」に移す\n"
            "- 新しいソースに、既存記事に書かれていない噂・確度の変化・新たな報道が1つもない場合は、"
            "記事を書かず「SKIP_NO_NEW_FACTS」とだけ出力すること\n\n"
            f"--- 既存記事 ---\n{existing_markdown}\n--- 既存記事ここまで ---\n\n"
        )
    else:
        task = "以下のソースから、噂まとめ記事を新しく書いてください。\n\n"

    user_message = (
        f"【重要】今日の日付: {today.strftime('%Y年%m月%d日')}\n"
        f"対象クラブ: {club['name']}\n"
        f"タイトルは「{club['title']}」とすること（変更しない）\n"
        f"「はじめに」先頭行の更新日は「{today.month}月{today.day}日」\n\n"
        f"{task}"
        f"{build_club_facts_text(config)}"
        f"--- ソース情報 ---\n{source_context}\n--- ここまで ---\n\n"
        "【情報源セクションの直後に以下を必ず追記すること】\n"
        "<!-- SEO\n"
        "seo_desc: ここに動きのあった噂と記事を読む価値が伝わる120文字以内の説明文\n"
        "-->"
    )

    http_client = httpx.Client(verify=_SSL_VERIFY) if not _SSL_VERIFY else None
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], http_client=http_client)
    response = client.messages.create(
        model=MODEL,
        max_tokens=10000,
        system=[{"type": "text", "text": RUMOR_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message}],
    )
    content = response.content[0].text
    has_title_line = re.search(r"^# ", content, flags=re.MULTILINE) is not None
    if "SKIP_NO_NEW_FACTS" in content and not has_title_line:
        print(f"[rumor] {club['name']}: 新しい動きなし → 更新しない")
        return GeneratedArticle(title="", content="SKIP_NO_NEW_FACTS", sources=search_results)

    content = _remove_meta_comments(content)
    content, meta_description = _extract_seo_meta(content)
    content = _normalize_player_names(content)
    # 検索評価を1つのURL・タイトルに集めるため、AIがタイトルを変えても固定タイトルに戻す
    content, n = re.subn(r"^# .*$", f"# {club['title']}", content, count=1, flags=re.MULTILINE)
    if n == 0 or "## 獲得の噂" not in content or is_too_short(content, config, "rumor"):
        print(f"[rumor] {club['name']}: フォーマット不正/内容不足 → スキップ")
        print(f"[rumor] AI応答冒頭: {content.strip().replace(chr(10), ' ')[:300]}")
        return GeneratedArticle(title="", content="SKIP_LOW_QUALITY", sources=search_results)

    print(f"[rumor] {club['name']}: 記事生成完了（input={response.usage.input_tokens}, output={response.usage.output_tokens}）")
    return GeneratedArticle(title=club["title"], content=content, sources=search_results, meta_description=meta_description)


def _is_due(updated_at: str, interval_days: int) -> bool:
    return datetime.fromisoformat(updated_at) <= datetime.now() - timedelta(days=interval_days) + timedelta(hours=1)


def run_rumor_roundups(
    conn: sqlite3.Connection,
    cfg: dict,
    dry_run: bool = False,
    config_path: str = "config.yaml",
    force: bool = False,
) -> int:
    """更新時期が来たクラブの噂まとめ記事を新規作成・更新し、件数を返す"""
    rumor_cfg = cfg.get("topic_rumors", {})
    clubs = rumor_cfg.get("clubs", [])
    if not clubs:
        return 0
    interval = rumor_cfg.get("update_interval_days", 2)
    cat_map = cfg.get("wordpress", {}).get("category_ids", {})
    init_rumor_table(conn)
    done = 0

    for club in clubs:
        saved = _get_rumor_post(conn, club["key"])
        if saved and not force and not _is_due(saved[1], interval):
            print(f"[rumor] {club['name']}: 前回更新から{interval}日未満のためスキップ")
            continue

        existing = fetch_post_for_update(saved[0], config_path) if saved else None
        if saved and existing is None:
            print(f"[rumor] {club['name']}: 記事(ID={saved[0]})が取得できないため新規作成")

        search_results, articles = _collect_sources(club, config_path)
        if len(articles) < MIN_SOURCES:
            print(f"[rumor] {club['name']}: ソース不足（{len(articles)}件）のためスキップ")
            continue

        existing_md = html_to_markdown_for_update(existing["html"])[0] if existing else None
        generated = generate_rumor_article(club, articles, search_results, existing_md, config_path)
        if generated.content.startswith("SKIP_"):
            if generated.content == "SKIP_NO_NEW_FACTS" and saved and not dry_run:
                _save_rumor_post(conn, club["key"], saved[0])  # 次回の確認は interval 日後
            continue

        if dry_run:
            print(f"\n[rumor] DRY RUN - {club['name']}")
            print("-" * 60)
            print(generated.content[:2000])
            print("-" * 60)
            done += 1
            continue

        cat_ids = [cat_map[s] for s in club.get("categories", ["transfers"]) if s in cat_map]
        if existing:
            result = publish_draft(
                generated.title, generated.content, config_path,
                category_ids=existing["categories"], meta_description=generated.meta_description,
                update_post_id=existing["id"],
            )
        else:
            featured_media_id = None
            img = fetch_image(club.get("image_query", club["name"]))
            if img:
                uploaded = upload_media(img[0], img[1], img[2], config_path)
                if uploaded:
                    featured_media_id = uploaded[0]
            result = publish_draft(
                generated.title, generated.content, config_path,
                featured_media_id=featured_media_id, category_ids=cat_ids,
                meta_description=generated.meta_description, slug=club.get("slug"),
            )
        _save_rumor_post(conn, club["key"], result.post_id)
        print(f"[rumor] {club['name']}: {'更新' if existing else '新規作成'}完了 ID={result.post_id} ({result.status}) {result.url}")
        done += 1
    return done


if __name__ == "__main__":
    # デバッグ用: python rumor_roundup.py [--dry-run]
    from dotenv import load_dotenv

    load_dotenv()
    cfg = load_config("config.yaml")
    conn = sqlite3.connect("processed.db")
    n = run_rumor_roundups(conn, cfg, dry_run="--dry-run" in sys.argv, force=True)
    print(f"[rumor] 完了: {n} 件")
