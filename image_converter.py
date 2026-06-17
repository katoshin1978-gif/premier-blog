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
    "western comic illustration style, bold ink outlines, "
    "vibrant saturated colors, dynamic composition, "
    "professional sports digital art, highly detailed"
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
    slug = re.sub(r"[^\w]", "_", topic[:40]).lower()
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
