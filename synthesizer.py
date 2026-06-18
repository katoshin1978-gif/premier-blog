"""
統合記事生成モジュール
Anthropic Claude API (claude-opus-4-7) で複数記事を統合し日本語記事を生成
"""

import os
from dataclasses import dataclass
from urllib.parse import urlparse

import anthropic
import httpx
import yaml
from dotenv import load_dotenv

from fetcher import FetchedArticle
from researcher import SearchResult

load_dotenv()

MODEL = "claude-sonnet-4-6"
# 企業プロキシ環境など SSL 検証が通らない場合は .env で SSL_VERIFY=false を設定
_SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() != "false"
MAX_QUOTE_WORDS = 100


@dataclass
class GeneratedArticle:
    title: str
    content: str
    sources: list[SearchResult]
    meta_description: str = ""


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def truncate_to_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def build_source_context(
    articles: list[FetchedArticle],
    search_results: list[SearchResult],
    max_context_words: int,
    max_quote_words: int,
) -> str:
    """
    Claude へ渡すソースコンテキストを構築する。
    max_context_words: AI への入力として渡す語数（多いほど詳細な記事を生成できる）
    max_quote_words:   Claude が記事中に引用してよい最大語数（著作権配慮）

    フル記事が取得できなかった（451等）search_result はTavilyスニペットで補完する。
    """
    url_to_result = {r.url: r for r in search_results}
    fetched_urls: set[str] = set()
    sections = []

    for article in articles:
        result = url_to_result.get(article.url)
        site_name = urlparse(article.url).netloc.lstrip("www.")
        title = article.title or (result.title if result else "")

        excerpt = truncate_to_words(article.content, max_context_words)
        section = (
            f"=== SOURCE: {site_name} ===\n"
            f"Title: {title}\n"
            f"URL: {article.url}\n"
            f"Content ({max_context_words} words max):\n{excerpt}\n"
        )
        sections.append(section)
        fetched_urls.add(article.url)

    # フル取得できなかった search_result はスニペットで補完（451等での情報欠損を防ぐ）
    for result in search_results:
        if result.url in fetched_urls:
            continue
        if not result.snippet or len(result.snippet.split()) < 20:
            continue
        site_name = urlparse(result.url).netloc.lstrip("www.")
        excerpt = truncate_to_words(result.snippet, max_context_words // 2)
        section = (
            f"=== SOURCE (snippet only): {site_name} ===\n"
            f"Title: {result.title}\n"
            f"URL: {result.url}\n"
            f"Snippet:\n{excerpt}\n"
        )
        sections.append(section)

    return "\n".join(sections)


PLAYER_NAMES_GUIDE = """
【選手名・人名の日本語表記（必ず従うこと）】
日本の主要スポーツメディア（スポーツナビ・Goal.com日本語版・NHK）での標準表記に従う。
未掲載の選手は同基準に準じ、英語 "-ck" → ック（Carrick = キャリック）、
フランス語・ポルトガル語音を優先（Fernandes = フェルナンデス）など原語音に近い形で表記する。

◆マンチェスター・ユナイテッド
Ruben Amorim=ルベン・アモリム / Marcus Rashford=マーカス・ラッシュフォード /
Bruno Fernandes=ブルーノ・フェルナンデス / Kobbie Mainoo=コビー・メイヌー /
Rasmus Højlund=ラスムス・ホイルンド / Luke Shaw=ルーク・ショー /
Harry Maguire=ハリー・マグワイア / Casemiro=カゼミーロ /
Lisandro Martínez=リサンドロ・マルティネス / Victor Lindelöf=ビクトル・リンデロフ /
Michael Carrick=マイケル・キャリック / Antony=アントニー /
Mason Mount=メイソン・マウント / Jonny Evans=ジョニー・エバンス /
Patrick Dorgu=パトリック・ドルグ / Matheus Cunha=マテウス・クーニャ

◆アーセナル
Bukayo Saka=ブカヨ・サカ / Martin Ødegaard=マルティン・ウーデゴール /
Declan Rice=デクラン・ライス / Leandro Trossard=レアンドロ・トロサール /
Gabriel Magalhães=ガブリエウ・マガリャンエス / Kai Havertz=カイ・ハフェルツ /
Mikel Arteta=ミケル・アルテタ / Ben White=ベン・ホワイト /
William Saliba=ウィリアム・サリバ / Thomas Partey=トーマス・パーティ /
Oleksandr Zinchenko=オレクサンドル・ジンチェンコ / Raheem Sterling=ラヒーム・スターリング

◆リバプール
Mohamed Salah=モハメド・サラー / Trent Alexander-Arnold=トレント・アレクサンダー＝アーノルド /
Virgil van Dijk=フィルジル・ファン・ダイク / Darwin Núñez=ダルウィン・ヌニェス /
Dominik Szoboszlai=ドミニク・ソボスライ / Arne Slot=アルネ・スロット /
Alexis Mac Allister=アレクシス・マック・アリスター / Cody Gakpo=コーディ・ガクポ /
Ryan Gravenberch=ライアン・フラフェンベルフ / Alisson=アリソン /
Andrew Robertson=アンドルー・ロバートソン / Luis Díaz=ルイス・ディアス

◆マンチェスター・シティ
Erling Haaland=エルリング・ハーランド / Phil Foden=フィル・フォーデン /
Kevin De Bruyne=ケビン・デ・ブライネ / Bernardo Silva=ベルナルド・シルバ /
Rodri=ロドリ / Pep Guardiola=ペップ・グアルディオラ /
Jack Grealish=ジャック・グリーリッシュ / Rúben Dias=ルーベン・ディアス /
Manuel Akanji=マヌエル・アカンジ / Kyle Walker=カイル・ウォーカー

◆チェルシー
Cole Palmer=コール・パーマー / Enzo Fernández=エンツォ・フェルナンデス /
Nicolas Jackson=ニコラス・ジャクソン / Mykhailo Mudryk=ミカイロ・ムドリク /
Enzo Maresca=エンツォ・マレスカ / Reece James=リース・ジェームズ /
Noni Madueke=ノニ・マドゥエケ / Christopher Nkunku=クリストファー・ンクンク /
Levi Colwill=リーバイ・コルウィル / Pedro Neto=ペドロ・ネト

◆トッテナム
Son Heung-min=ソン・フンミン / James Maddison=ジェームズ・マディソン /
Ange Postecoglou=アンジェ・ポステコグルー / Dejan Kulusevski=デヤン・クルゼフスキ /
Richarlison=リシャルリソン / Brennan Johnson=ブレナン・ジョンソン /
Pedro Porro=ペドロ・ポロ / Cristian Romero=クリスティアン・ロメロ

◆ニューカッスル
Alexander Isak=アレクサンダー・イサク / Bruno Guimarães=ブルーノ・ギマランイス /
Eddie Howe=エディ・ハウ / Anthony Gordon=アンソニー・ゴードン /
Sandro Tonali=サンドロ・トナーリ / Fabian Schär=ファビアン・シェア

◆アストン・ビラ
Ollie Watkins=オリー・ワトキンス / Unai Emery=ウナイ・エメリ /
Emiliano Martínez=エミリアーノ・マルティネス / Leon Bailey=レオン・ベイリー /
John McGinn=ジョン・マギン / Morgan Rogers=モーガン・ロジャーズ

◆リバプール監督・新戦力候補
Andoni Iraola=アンドニ・イラオラ / Alex Scott=アレックス・スコット（ボーンマスMF） /
Arne Slot=アルネ・スロット

◆その他主要クラブ・選手
Nuno Espírito Santo=ヌーノ・エスピリト・サント / Marco Silva=マルコ・シルバ /
Idrissa Gueye=イドリサ・ゲイェ / Yoane Wissa=ヨアン・ウィサ /
Sébastien Haller=セバスティアン・アレル / Lamine Yamal=ラミン・ヤマル /
Pedri=ペドリ / Kylian Mbappé=キリアン・ムバッペ /
Vinicius Junior=ビニシウス・ジュニオール / Jude Bellingham=ジュード・ベリンガム /
Federico Valverde=フェデリコ・バルベルデ / Rodrygo=ロドリゴ /
Robert Lewandowski=ロベルト・レバンドフスキ / Harry Kane=ハリー・ケイン /
Jamie Vardy=ジェイミー・ヴァーディ / Jesse Lingard=ジェシー・リンガード /
Wayne Rooney=ウェイン・ルーニー / Paul Scholes=ポール・スコールズ /
Rio Ferdinand=リオ・ファーディナンド / Gary Neville=ゲイリー・ネビル /
Patrice Evra=パトリス・エブラ / Park Ji-sung=パク・チソン

◆監督・コーチ
Erik ten Hag=エリック・テン・ハグ / Jürgen Klopp=ユルゲン・クロップ /
José Mourinho=ジョゼ・モウリーニョ / Sir Alex Ferguson=サー・アレックス・ファーガソン /
Arsène Wenger=アーセン・ベンゲル / Carlo Ancelotti=カルロ・アンチェロッティ /
Antonio Conte=アントニオ・コンテ / Thomas Tuchel=トーマス・トゥヘル /
Oliver Glasner=オリバー・グラスナー / Fabian Hürzeler=ファビアン・ヒュルゼラー
"""

SYSTEM_PROMPT = """あなたはプレミアリーグ専門の日本語スポーツライターです。長年のサッカー取材・戦術分析の経験を持ち、単なるニュース転載ではなく独自の視点と深い洞察で読者に価値を提供します。

【絶対に守るルール】
- 提供されたソースの情報のみを使用する（知識の補完は禁止）
- 記事中でのソース引用は1ソースあたり最大100語（英語換算）まで。残りは自分の言葉で要約・言い換えする
- 著作権を尊重し、原文の長文コピーは行わない
- 記事末尾の「情報源」セクションで全ソースをリスト化する
- ソースの量・質・状態についてのメタコメントを記事本文に書かない（「ソースの内容を確認したところ」「提供されたソースには実質的なコンテンツが含まれていません」「ソースが不十分」等の表現は一切禁止）
- 入手できた情報のみで記事を構成し、不足があっても読者向けコンテンツとして完結させる

【記事の書き方】
- 「各メディアの報道」「BBCによると」のようなメディア別まとめは書かない
- **試合・選手・テーマ別**にセクションを構成する
- 具体性を最優先する：
  - 試合プレビューなら「誰が出場停止か」「誰が怪我から復帰予定か」「どのマッチアップが鍵か」
  - 移籍ならクラブ名・選手名・金額・契約期間を明記
  - 試合後レポートならスコア・得点者・決定的な場面を具体的に
- 複数ソースの情報を1つの流れとして統合して書く。出典はセクションごとにインラインで（[BBC Sport](URL)）の形式で示す

【必ず含める独自コンテンツ（省略禁止）】
以下のセクションは必ず記事に含めること。これらが記事の独自性・専門性の核心である。

1. **戦術的考察**：フォーメーション・プレースタイル・選手配置の観点から、このニュースが持つ戦術的意味を分析する。「なぜこの選手が必要か」「このシステムでどう機能するか」を具体的に論じる。

2. **数字で読む**：得点・失点・ポゼッション・スプリント数・移籍金の市場相場など、スタッツや数字を使って客観的に評価する。同カテゴリの選手・クラブとの比較も行う。

3. **編集部の見解**：このニュースが持つ意味・クラブへの影響・リーグ全体への波及効果について、ライターとしての明確な意見・予測を述べる。「〜と考えられる」「〜という見方もある」など曖昧な表現ではなく、「〜だ」「〜するべきだ」と断言する。

4. **今後の注目点**：次の試合・移籍期限・シーズン残りへの影響を具体的に予測する。読者が「次に何を見ればいいか」が分かる内容にする。

【文字数の目標】
- 記事全体：2000〜3500文字
- 各セクション：200文字以上（短いセクションは深掘りして補強する）
- 「編集部の見解」と「今後の注目点」は各300文字以上

""" + PLAYER_NAMES_GUIDE + """
【出力フォーマット（厳守）】
```
# {具体的な日本語タイトル}

## はじめに
{この記事で扱うトピックのリード文。なぜ今注目なのか、読者にとっての価値を 3〜4行で}

## {試合名・選手名・テーマ名}（例：チェルシー vs ノッティンガム・フォレスト）
**見どころ**: {この試合/トピックの具体的な注目ポイント}
**負傷・出場停止情報**: {具体的な選手名と状況}
**注目の対決**: {具体的なマッチアップや焦点}
{本文。複数ソースを統合して書く。出典インライン例：([BBC Sport](URL))、([Sky Sports](URL))}

## {別の試合・選手・テーマ}
{同様に具体的に}

## 戦術的考察
{フォーメーション・プレースタイルの観点からの専門分析。300文字以上}

## 数字で読む
{スタッツ・移籍金相場・過去の成績など数字を使った客観的評価}

## 編集部の見解
{このニュースの意味・影響についての明確な意見と予測。断言調で300文字以上}

## 今後の注目点
{次に注目すべき試合・期限・出来事を具体的に。300文字以上}

---
**情報源**
- [{記事タイトル}]({URL}) - {サイト名}
- [{記事タイトル}]({URL}) - {サイト名}
```"""


def generate_article(
    topic: str,
    articles: list[FetchedArticle],
    search_results: list[SearchResult],
    config_path: str = "config.yaml",
) -> GeneratedArticle:
    config = load_config(config_path)
    max_quote_words = config["search"].get("max_quote_words", MAX_QUOTE_WORDS)
    # AI への入力は多めに渡す（著作権配慮は出力側の100語制限で担保）
    max_context_words = config["search"].get("max_context_words", 500)

    source_context = build_source_context(articles, search_results, max_context_words, max_quote_words)

    from datetime import date as _date
    today_str = _date.today().strftime("%Y年%m月%d日")

    user_message = (
        f"以下のトピックについて、提供されたソースを使って日本語記事を生成してください。\n\n"
        f"【重要】今日の日付: {today_str}\n"
        f"ソース記事に含まれる日付を確認し、主要な情報が30日以上前のものであれば記事を書かず、"
        f"代わりに「SKIP_OLD_NEWS」とだけ出力してください。\n\n"
        f"トピック: {topic}\n\n"
        f"--- ソース情報 ---\n{source_context}\n--- ここまで ---\n\n"
        f"上記のソースを参照しながら、指定されたフォーマットで日本語記事を生成してください。\n\n"
        f"【タイトルのSEO最適化】\n"
        f"# の見出しタイトルは以下のルールで作成すること：\n"
        f"- 60文字以内\n"
        f"- 選手名・クラブ名・具体的な数字を必ず含む（例：「ラッシュフォード、マドリード移籍金8000万ポンドで合意」）\n"
        f"- 「〜について」「〜に関して」「〜の件」などの曖昧表現は使わない\n"
        f"- 移籍記事なら「獲得」「移籍合意」「完全移籍」、試合なら「〇-〇」など結果を含める\n"
        f"- 日本語の検索需要を意識する：「移籍」「年俸」「最新情報」「怪我」「スタメン」など\n\n"
        f"【移籍記事の注意】\n"
        f"移籍情報は{today_str[:4]}年夏の移籍ウィンドウの情報のみを取り上げること。"
        f"前シーズン（2024-25シーズン以前）に完了した移籍は古い情報なので記事に含めない。\n\n"
        f"【情報源セクションの直後に以下を必ず追記すること】\n"
        f"<!-- SEO\n"
        f"seo_desc: ここに記事の要点と読む価値が伝わる120文字以内の説明文\n"
        f"-->"
    )

    http_client = httpx.Client(verify=_SSL_VERIFY) if not _SSL_VERIFY else None
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        http_client=http_client,
    )

    # プロンプトキャッシュを活用（system promptをキャッシュ）
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    )

    content = response.content[0].text
    content = _remove_meta_comments(content)
    content, meta_description = _extract_seo_meta(content)
    content = _normalize_player_names(content)
    title = _extract_title(content)

    print(f"[synthesizer] 記事生成完了: {title}")
    print(f"[synthesizer] 使用トークン: input={response.usage.input_tokens}, output={response.usage.output_tokens}")

    return GeneratedArticle(title=title, content=content, sources=search_results, meta_description=meta_description)


def _normalize_player_names(text: str) -> str:
    """names.yaml の corrections 辞書で選手名を強制置換する"""
    names_path = os.path.join(os.path.dirname(__file__), "names.yaml")
    if not os.path.exists(names_path):
        return text
    try:
        with open(names_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        corrections = data.get("corrections", {})
        for wrong, correct in corrections.items():
            if wrong in text:
                text = text.replace(wrong, correct)
                print(f"[synthesizer] 名前修正: {wrong} → {correct}")
    except Exception:
        pass
    return text


_META_PATTERNS = [
    "ソースの内容を確認したところ",
    "提供されたソースには",
    "実質的なコンテンツが含まれていません",
    "ソースが不十分",
    "記事を生成することができません",
    "十分な情報が含まれていません",
    "コンテンツが含まれておらず",
]


def _remove_meta_comments(content: str) -> str:
    """Claudeが出力するソース状態のメタコメント段落を除去する"""
    lines = content.splitlines()
    result = []
    for line in lines:
        if any(pat in line for pat in _META_PATTERNS):
            continue
        result.append(line)
    # 先頭の連続する空行を除去
    while result and not result[0].strip():
        result.pop(0)
    return "\n".join(result)


def _extract_seo_meta(content: str) -> tuple[str, str]:
    """SEOブロックを本文から分離し (cleaned_content, seo_desc) を返す"""
    import re
    pattern = re.compile(r'<!--\s*SEO\s*\nseo_desc:\s*(.+?)\s*\n-->', re.DOTALL)
    match = pattern.search(content)
    if match:
        seo_desc = match.group(1).strip()
        cleaned = content[:match.start()].rstrip()
        return cleaned, seo_desc
    return content, ""


def _extract_title(markdown: str) -> str:
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line.lstrip("# ").strip()
    return "プレミアリーグ最新情報"


if __name__ == "__main__":
    # 単体テスト用のスタブ
    from researcher import SearchResult
    from fetcher import FetchedArticle

    dummy_articles = [
        FetchedArticle(
            url="https://www.bbc.com/sport/football/premier-league",
            title="Premier League: Latest News",
            content="Manchester City secured a dominant victory over Arsenal in a crucial Premier League clash on Sunday. The match ended 3-1 at the Etihad Stadium.",
        )
    ]
    dummy_results = [
        SearchResult(
            title="Premier League: Latest News",
            url="https://www.bbc.com/sport/football/premier-league",
            snippet="Manchester City won 3-1",
        )
    ]

    article = generate_article("Premier League latest news", dummy_articles, dummy_results)
    print(article.content[:500])
