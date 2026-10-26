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


INJURY_SYSTEM_PROMPT = """あなたはプレミアリーグ専門の日本語スポーツライターです。
特定クラブの負傷者・出場停止者・復帰状況を、一目で分かる「怪我人情報まとめ」記事として整理します。

【絶対に守るルール】
- 提供されたソース（と既存記事）の情報のみを使用する（知識の補完は禁止）
- 負傷の部位・程度・復帰時期は、ソースに書かれている場合だけ書く。推測で書かない。
  復帰時期の記載がなければ「未定」、負傷の詳細が非公表なら「負傷（詳細非公表）」とする
- 監督会見の発言（"assessed" "touch and go" "a few weeks" 等）は、その確度のまま「様子見」「数週間の見込み」のように訳す。断定を強めない
- 日付は「M月D日」形式で、ソースの「Published:」の日付か既存記事に書かれた日付だけを使う。推測で日付を付けない。
  「上旬」「下旬」「ごろ」のようなぼかした日付も推測にあたるため禁止。日付が分からなければ書かない
- 「Published:」の日付は記事が出た日であって、試合や負傷の日ではない。「M月D日の報道では」の形でだけ使い、
  試合・負傷・会見の日付として書かない。試合や出来事の日付は、ソース本文に日付が明記されている場合だけ書く
- ソース同士で情報が食い違う場合は、「Published:」の日付が新しいソースを優先する。
  直近の試合でベンチ入り・出場した記録がある選手は、それより古い「離脱中」の報道があっても「復帰した選手」に入れる
- 復帰した選手について「まだ実戦に出ていない」「次節から実戦復帰の可能性」のように、出場していないと決めつける書き方は、
  ソースがそう明言している場合だけ使う。ソースで確認できた最新の出場・ベンチ入りの記録だけを書く
- 掲載するのは負傷・病気・出場停止だけ。スタメン落ち・ベンチ外し・構想外など、起用上の理由による不出場は載せない
- 「はじめに」は負傷・復帰の話だけに絞る。戦績・監督の進退など、怪我と関係のない話は書かない
- 記事中でのソース引用は1ソースあたり最大100語（英語換算）まで。残りは自分の言葉で要約・言い換えする
- 著作権を尊重し、原文の長文コピーは行わない
- ソースの量・質・状態についてのメタコメントを記事本文に書かない
- 記事末尾の「情報源」セクションで、使ったソースをすべてリスト化する

【記事の書き方】
- 離脱が長い・影響が大きい選手から順に並べる
- 各選手には、欠場がチームにどう響くか（誰が代わりに出るか）を1〜2文で添える

【文字数の目標】
- 記事全体：1800〜3500文字

""" + PLAYER_NAMES_GUIDE + """
【出力フォーマット（厳守）】
```
# {タイトル}

## はじめに
**【{M月D日}更新】** {今回の更新で動きのあった負傷・復帰の要点を1文で}
{次の試合の予定と、離脱状況の全体像を2〜3行で}

## 負傷・離脱中の選手
### {選手名}（{ポジション}）
- **状態**: {負傷内容}
- **きっかけ**: {〇〇戦で負傷 等。試合名だけを書き日付は付けない。分からなければこの行を省く}
- **復帰見込み**: {ソースに書かれた時期 / 未定}
- **主な報道**: {媒体名}（{M月D日}）
{欠場の影響1〜2文。出典インライン例：([BBC Sport](URL))}

## 出場停止
- **{選手名}**：{理由と残り試合数}（{M月D日}）

## 復帰した選手
- **{選手名}**：{〇〇戦でベンチ入り・出場 等}（{M月D日の報道}）

## 戦力への影響
{離脱者を踏まえた布陣・選手起用の見通し。断言調で300文字以上}

## 今後の注目点
{次に復帰が見込まれる選手・確認すべき会見など。200文字以上}

---
**情報源**
- [{記事タイトル}]({URL}) - {サイト名}
```
該当者がいない節（出場停止・復帰した選手）は見出しごと省いてよい。"""


# まとめ記事の種類ごとの設定（config のキー・プロンプト・必須の節・更新時の指示）
KINDS = {
    "rumors": {
        "config_key": "topic_rumors",
        "label": "噂まとめ",
        "system_prompt": RUMOR_SYSTEM_PROMPT,
        "search_context": "transfers",
        "required_sections": ["## 獲得の噂", "## 放出の噂"],
        "new_task": "以下のソースから、噂まとめ記事を新しく書いてください。\n\n",
        "update_rules": (
            "- 新しいソースに出てきた噂を追加し、既存の噂は確度・報道を最新に更新する\n"
            "- 既存記事の噂は、新しいソースで否定されていない限り残してよい。ただし「主な報道」の最新の日付が"
            "今日から30日以上前の噂は「決着・立ち消えになった噂」に移す\n"
            "- 移籍が決定した・破談になった噂は「決着・立ち消えになった噂」に移す\n"
            "- 新しいソースに、既存記事に書かれていない噂・確度の変化・新たな報道が1つもない場合は、"
            "記事を書かず「SKIP_NO_NEW_FACTS」とだけ出力すること\n\n"
        ),
        "seo_hint": "動きのあった噂と記事を読む価値",
    },
    "injuries": {
        "config_key": "topic_injuries",
        "label": "怪我人まとめ",
        "system_prompt": INJURY_SYSTEM_PROMPT,
        "search_context": "default",
        "required_sections": ["## 負傷・離脱中の選手", "## 出場停止", "## 復帰した選手"],
        "new_task": "以下のソースから、怪我人情報まとめ記事を新しく書いてください。\n\n",
        "update_rules": (
            "- 新しいソースの負傷・出場停止・復帰の情報で、既存記事の各選手の状態を最新に更新する\n"
            "- 新しいソースで復帰が報じられた選手は「復帰した選手」に移す。「復帰した選手」のうち復帰から"
            "14日以上たった選手は記事から外す\n"
            "- 新しいソースに言及がなく、「主な報道」の最新の日付が今日から21日以上前の負傷者は、"
            "状態に「最新情報なし（M月D日時点）」と明記して残す\n"
            "- 新しいソースに、既存記事に書かれていない負傷・復帰・出場停止・復帰時期の変化が1つもない場合は、"
            "記事を書かず「SKIP_NO_NEW_FACTS」とだけ出力すること\n\n"
        ),
        "seo_hint": "動きのあった負傷・復帰の状況と記事を読む価値",
    },
}


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


def _collect_sources(club: dict, config_path: str, context: str) -> tuple[list, list]:
    """クラブごとの検索語を複数回投げてソースを集める（同一URLは除外）"""
    results = []
    seen: set[str] = set()
    for q in club["queries"]:
        for r in search_articles(q, config_path, context=context):
            if r.url not in seen:
                seen.add(r.url)
                results.append(r)
    articles = fetch_articles([r.url for r in results]) if results else []
    return results, articles


def _fix_returned_players(
    client: anthropic.Anthropic, k: dict, user_message: str, content: str, source_context: str, club_name: str,
) -> str:
    """「負傷・離脱中」に載せた選手のうち、ソースに出場・ベンチ入りの記録がある選手を洗い出し、
    いれば「復帰した選手」に移して書き直させる（復帰済みの選手を負傷中と書く誤りの防止）"""
    m = re.search(r"## 負傷・離脱中の選手\n(.*?)(?=\n## )", content, flags=re.DOTALL)
    if not m:
        return content
    check = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": (
            "次の「負傷・離脱中の選手」に載っている選手のうち、負傷・離脱が報じられた後の試合で出場した、"
            "またはベンチ入りした（starting line-up / XI / subs / substitutes / came on 等）とソースに書かれている選手を挙げてください。\n"
            "負傷する前の試合や、その試合で負傷した試合での出場は挙げない（例：トッテナム戦に先発してその試合で負傷した選手は該当しない）。"
            "その出場より後に、再び欠場・離脱が報じられている選手も挙げない。"
            "予想スタメン（predicted line-up）は根拠にしない。\n"
            "最後の行に結果だけを書く。選手ごとに「選手名：試合名で先発／途中出場／ベンチ入り（根拠の媒体名とPublished日付）」を"
            "「 / 」区切りで1行にまとめる。該当者がいなければ最後の行を NONE とする。\n\n"
            f"--- 負傷・離脱中の選手 ---\n{m.group(1)}\n\n--- ソース ---\n{source_context}"
        )}],
    )
    lines = [l for l in check.content[0].text.strip().splitlines() if l.strip()]
    found = lines[-1].strip() if lines else ""
    if not found or "NONE" in found:
        return content
    print(f"[rumor] {club_name}: 負傷中としていた選手に出場・ベンチ入りの記録あり → 書き直し\n{found}")
    fixed = client.messages.create(
        model=MODEL,
        max_tokens=10000,
        system=[{"type": "text", "text": k["system_prompt"], "cache_control": {"type": "ephemeral"}}],
        messages=[
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": content},
            {"role": "user", "content": (
                "確認の結果、次の選手はソースで試合に出場またはベンチ入りしている。"
                "これらの選手を「負傷・離脱中の選手」から外して「復帰した選手」に移し、"
                "「はじめに」「戦力への影響」「今後の注目点」の記述も合わせて直した記事の全文を、同じ出力フォーマットで出力すること。\n\n"
                f"{found}"
            )},
        ],
    )
    text = fixed.content[0].text
    return text if re.search(r"^# ", text, flags=re.MULTILINE) else content


def generate_rumor_article(
    club: dict,
    articles: list,
    search_results: list,
    existing_markdown: str | None,
    config_path: str = "config.yaml",
    kind: str = "rumors",
) -> GeneratedArticle:
    """まとめ記事（噂・怪我人）を生成する。既存記事があれば最新ソースで書き直す。
    新しい動きがなければ content="SKIP_NO_NEW_FACTS" を返す"""
    k = KINDS[kind]
    config = load_config(config_path)
    max_quote_words = config["search"].get("max_quote_words", 100)
    max_context_words = config["search"].get("max_context_words", 500)
    source_context = build_source_context(articles, search_results, max_context_words, max_quote_words)
    today = date.today()

    if existing_markdown:
        task = (
            f"既に公開している{k['label']}記事を、新しいソースで最新の状態に書き直してください。\n"
            f"{k['update_rules']}"
            f"--- 既存記事 ---\n{existing_markdown}\n--- 既存記事ここまで ---\n\n"
        )
    else:
        task = k["new_task"]

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
        f"seo_desc: ここに{k['seo_hint']}が伝わる120文字以内の説明文\n"
        "-->"
    )

    http_client = httpx.Client(verify=_SSL_VERIFY) if not _SSL_VERIFY else None
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], http_client=http_client)
    response = client.messages.create(
        model=MODEL,
        max_tokens=10000,
        system=[{"type": "text", "text": k["system_prompt"], "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message}],
    )
    content = response.content[0].text
    has_title_line = re.search(r"^# ", content, flags=re.MULTILINE) is not None
    if "SKIP_NO_NEW_FACTS" in content and not has_title_line:
        print(f"[rumor] {club['name']}: 新しい動きなし → 更新しない")
        return GeneratedArticle(title="", content="SKIP_NO_NEW_FACTS", sources=search_results)

    if kind == "injuries" and has_title_line:
        content = _fix_returned_players(client, k, user_message, content, source_context, club["name"])

    content = _remove_meta_comments(content)
    content, meta_description = _extract_seo_meta(content)
    content = _normalize_player_names(content)
    # 検索評価を1つのURL・タイトルに集めるため、AIがタイトルを変えても固定タイトルに戻す
    content, n = re.subn(r"^# .*$", f"# {club['title']}", content, count=1, flags=re.MULTILINE)
    # 処分で主力流出が話題のクラブ等は放出の噂だけになることもあるため、必須の節はどれか1つあればよい
    has_section = any(s in content for s in k["required_sections"])
    if n == 0 or not has_section or is_too_short(content, config, "rumor"):
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
    kind: str = "rumors",
) -> int:
    """更新時期が来たクラブのまとめ記事（kind: rumors / injuries）を新規作成・更新し、件数を返す"""
    k = KINDS[kind]
    rumor_cfg = cfg.get(k["config_key"], {})
    clubs = rumor_cfg.get("clubs", [])
    if not clubs:
        return 0
    interval = rumor_cfg.get("update_interval_days", 7)
    cat_map = cfg.get("wordpress", {}).get("category_ids", {})
    init_rumor_table(conn)
    done = 0

    for club in clubs:
        # 噂まとめは既存の記録とキーを揃えるためクラブキーのまま、それ以外は種類を前置する
        db_key = club["key"] if kind == "rumors" else f"{kind}:{club['key']}"
        saved = _get_rumor_post(conn, db_key)
        if saved and not force and not _is_due(saved[1], interval):
            print(f"[rumor] {club['name']}: 前回更新から{interval}日未満のためスキップ")
            continue

        existing = fetch_post_for_update(saved[0], config_path) if saved else None
        if saved and existing is None:
            print(f"[rumor] {club['name']}: 記事(ID={saved[0]})が取得できないため新規作成")

        search_results, articles = _collect_sources(club, config_path, k["search_context"])
        if len(articles) < MIN_SOURCES:
            print(f"[rumor] {club['name']}: ソース不足（{len(articles)}件）のためスキップ")
            continue

        existing_md = html_to_markdown_for_update(existing["html"])[0] if existing else None
        generated = generate_rumor_article(club, articles, search_results, existing_md, config_path, kind)
        if generated.content.startswith("SKIP_"):
            if generated.content == "SKIP_NO_NEW_FACTS" and saved and not dry_run:
                _save_rumor_post(conn, db_key, saved[0])  # 次回の確認は interval 日後
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
        _save_rumor_post(conn, db_key, result.post_id)
        print(f"[rumor] {club['name']}: {'更新' if existing else '新規作成'}完了 ID={result.post_id} ({result.status}) {result.url}")
        done += 1
    return done


if __name__ == "__main__":
    # デバッグ用: python rumor_roundup.py [--kind injuries] [--club man-utd] [--dry-run] [--force]
    import argparse

    from dotenv import load_dotenv

    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="rumors", choices=list(KINDS))
    ap.add_argument("--club", help="対象クラブのキー（省略時は全クラブ）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="更新間隔に関係なく更新する")
    args = ap.parse_args()
    cfg = load_config("config.yaml")
    if args.club:
        kcfg = cfg[KINDS[args.kind]["config_key"]]
        kcfg["clubs"] = [c for c in kcfg["clubs"] if c["key"] == args.club]
    conn = sqlite3.connect("processed.db")
    n = run_rumor_roundups(conn, cfg, dry_run=args.dry_run, force=args.force, kind=args.kind)
    print(f"[rumor] 完了: {n} 件")
