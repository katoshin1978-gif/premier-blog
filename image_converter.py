"""
Replicate APIを使った画像イラスト変換モジュール。
アイキャッチ: Flux-schnell でテキスト→イラスト生成
選手写真: Flux-dev img2img でイラスト変換
"""

import io
import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()

_SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() != "false"

# 企業プロキシ環境でのSSL証明書エラー回避
if not _SSL_VERIFY:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_ILLUSTRATION_STYLE = (
    "hyperrealistic sports art, professional digital painting, "
    "dramatic stadium lighting, vivid colors, sharp focus, 4K detail"
)


def _download_output(url: str) -> bytes | None:
    try:
        resp = requests.get(str(url), timeout=60, verify=_SSL_VERIFY)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"[image_converter] ダウンロード失敗: {e}")
        return None


def _get_replicate_client():
    import replicate
    import httpx
    if not _SSL_VERIFY:
        transport = httpx.HTTPTransport(verify=False)
        return replicate.Client(
            api_token=os.environ.get("REPLICATE_API_TOKEN"),
            transport=transport,
        )
    return replicate.default_client


def generate_featured_image(topic: str) -> tuple[bytes, str, str] | None:
    """
    トピックからアイキャッチイラストをFlux-schnellで生成。
    Returns (image_bytes, filename, attribution) or None.
    """
    try:
        import replicate  # noqa: F401
    except ImportError:
        print("[image_converter] replicate ライブラリが未インストール")
        return None

    prompt = (
        f"Premier League football scene, {topic}, "
        f"{_ILLUSTRATION_STYLE}, wide cinematic composition"
    )
    slug = re.sub(r"[^a-zA-Z0-9]", "_", topic[:40]).lower()
    filename = f"illustration_{slug}.jpg"

    try:
        client = _get_replicate_client()
        output = client.run(
            "black-forest-labs/flux-schnell",
            input={
                "prompt": prompt,
                "aspect_ratio": "16:9",
                "output_format": "jpg",
                "num_outputs": 1,
            }
        )
        content = _download_output(output[0])
        if content:
            print(f"[image_converter] アイキャッチ生成完了: {filename}")
            return content, filename, "Generated with Flux (Replicate)"
    except Exception as e:
        print(f"[image_converter] アイキャッチ生成失敗: {e}")

    return None


def generate_logo_image(title: str) -> tuple[bytes, str, str] | None:
    """
    タイトル文字列からロゴ風画像をPillowで生成。
    Wikimediaで写真が取れない汎用トピック（transfer news等）向け。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[image_converter] Pillowが未インストール")
        return None

    # カテゴリ判定（タイトルのキーワードで色とラベルを決定）
    tl = title.lower()
    if any(k in tl for k in ["transfer", "移籍", "signing", "move"]):
        label, sub_label = "TRANSFER\nNEWS", "SUMMER WINDOW 2026"
        bg, accent = "#0d1117", "#f0c040"
    elif any(k in tl for k in ["europe", "champions", "europa", "欧州", "cl"]):
        label, sub_label = "EUROPEAN\nFOOTBALL", "PREMIER BLOG"
        bg, accent = "#0d1117", "#e74c3c"
    elif any(k in tl for k in ["match", "review", "analysis", "試合", "分析"]):
        label, sub_label = "MATCH\nREVIEW", "PREMIER BLOG"
        bg, accent = "#0d1117", "#2ecc71"
    else:
        label, sub_label = "PREMIER\nLEAGUE", "PREMIER BLOG"
        bg, accent = "#0d1117", "#7b5ea7"

    W, H = 1280, 720
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    # 右端にアクセントブロック
    draw.rectangle([W - 12, 0, W, H], fill=accent)
    draw.rectangle([W - 28, 0, W - 16, H], fill=accent + "60" if len(accent) == 7 else accent)

    # 下部ラインバー
    draw.rectangle([0, H - 10, W, H], fill=accent)
    draw.rectangle([0, H - 22, W, H - 14], fill=accent + "80" if len(accent) == 7 else accent)

    # フォント設定（環境依存：Windows→Linux→デフォルトの順）
    font_paths_large = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    font_paths_small = [
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]

    font_large = None
    for fp in font_paths_large:
        try:
            font_large = ImageFont.truetype(fp, 160)
            break
        except Exception:
            continue
    if font_large is None:
        try:
            font_large = ImageFont.load_default(size=160)
        except Exception:
            font_large = ImageFont.load_default()

    font_small = None
    for fp in font_paths_small:
        try:
            font_small = ImageFont.truetype(fp, 38)
            break
        except Exception:
            continue
    if font_small is None:
        try:
            font_small = ImageFont.load_default(size=38)
        except Exception:
            font_small = ImageFont.load_default()

    # メインラベル（中央配置）
    bbox = draw.textbbox((0, 0), label, font=font_large, align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (W - tw) // 2 - 20
    y = (H - th) // 2 - 40
    # シャドウ
    draw.text((x + 4, y + 4), label, font=font_large, fill="#00000060", align="center")
    draw.text((x, y), label, font=font_large, fill=accent, align="center")

    # サブラベル
    sbbox = draw.textbbox((0, 0), sub_label, font=font_small)
    sw = sbbox[2] - sbbox[0]
    draw.text(((W - sw) // 2 - 20, H - 65), sub_label, font=font_small, fill="#ffffff80")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)

    slug = re.sub(r"[^a-zA-Z0-9]", "_", label.replace("\n", "_"))
    filename = f"logo_{slug}.jpg"
    print(f"[image_converter] ロゴ画像生成完了: {filename}")
    return buf.getvalue(), filename, "Premier Blog Original"


def pad_to_landscape(image_bytes: bytes, target_ratio: float = 16 / 9) -> bytes:
    """
    縦長・正方形画像を、元画像をぼかした背景で左右を埋めて横長に変換する。
    既に target_ratio 以上の横長なら何もしない。
    """
    from PIL import Image, ImageFilter
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if w / h >= target_ratio:
        return image_bytes
    target_w = int(h * target_ratio)
    bg = img.resize((target_w, h), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
    x_offset = (target_w - w) // 2
    bg.paste(img, (x_offset, 0))
    out = io.BytesIO()
    bg.save(out, format="JPEG", quality=85)
    print(f"[image_converter] pad_to_landscape: {w}x{h} → {target_w}x{h}")
    return out.getvalue()


def convert_to_realistic_featured(
    image_bytes: bytes, filename: str, title: str = ""
) -> tuple[bytes, str] | None:
    """
    横長写真をFlux-devでフォトリアルなサッカーアートに変換（アイキャッチ用）。
    prompt_strength=0.65でリアルアート調への変換強度を高める。
    """
    try:
        import replicate  # noqa: F401
    except ImportError:
        print("[image_converter] replicate ライブラリが未インストール")
        return None

    subject = title[:80] if title else "football player"
    prompt = (
        f"Premier League football, {subject}, "
        "hyperrealistic sports photography art, professional digital painting, "
        "dramatic stadium lighting, sharp focus, vivid colors, 4K ultra detail"
    )
    new_filename = "art_" + re.sub(r"[^a-zA-Z0-9]", "_", filename[:40]).lower() + ".jpg"

    try:
        client = _get_replicate_client()
        output = client.run(
            "black-forest-labs/flux-dev",
            input={
                "prompt": prompt,
                "image": io.BytesIO(image_bytes),
                "prompt_strength": 0.9,
                "output_format": "jpg",
                "num_outputs": 1,
                "guidance": 3.5,
                "num_inference_steps": 28,
            }
        )
        content = _download_output(output[0])
        if content:
            print(f"[image_converter] アイキャッチ変換完了: {new_filename}")
            return content, new_filename
    except Exception as e:
        print(f"[image_converter] アイキャッチ変換失敗: {e}")

    return None


def convert_to_illustration(
    image_bytes: bytes, filename: str, player_name: str = ""
) -> tuple[bytes, str] | None:
    """
    選手写真をFlux-dev img2imgでイラスト変換。
    Returns (converted_bytes, new_filename) or None（失敗時は呼び出し元で元画像を使う）.
    """
    try:
        import replicate  # noqa: F401
    except ImportError:
        print("[image_converter] replicate ライブラリが未インストール")
        return None

    subject = f"football player {player_name}" if player_name else "football player"
    prompt = f"{subject}, {_ILLUSTRATION_STYLE}, portrait illustration"
    new_filename = "illust_" + filename

    try:
        client = _get_replicate_client()
        output = client.run(
            "black-forest-labs/flux-dev",
            input={
                "prompt": prompt,
                "image": io.BytesIO(image_bytes),
                "prompt_strength": 0.65,
                "output_format": "jpg",
                "num_outputs": 1,
                "guidance": 3.5,
                "num_inference_steps": 28,
            }
        )
        content = _download_output(output[0])
        if content:
            print(f"[image_converter] 変換完了: {player_name} → {new_filename}")
            return content, new_filename
    except Exception as e:
        print(f"[image_converter] 変換失敗 ({player_name}): {e}")

    return None
