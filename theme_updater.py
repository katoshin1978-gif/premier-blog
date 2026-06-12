"""
テーマファイル更新ユーティリティ。
差分表示 → 確認 → プッシュ の流れを強制する。
"""
import difflib
import io
import os
import zipfile

import requests

ZIP_PATH = "premier-blog-theme.zip"


def _read_from_zip(filename: str) -> str:
    with zipfile.ZipFile(ZIP_PATH) as z:
        return z.read(filename).decode("utf-8")


def _write_to_zip(filename: str, new_content: str) -> None:
    with zipfile.ZipFile(ZIP_PATH) as z:
        all_files = {n: z.read(n) for n in z.namelist()}
    all_files[filename] = new_content.encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, d in all_files.items():
            zout.writestr(n, d)
    with open(ZIP_PATH, "wb") as f:
        f.write(buf.getvalue())


def push_with_diff(
    filename: str,
    new_content: str,
    auth_headers: dict,
    base_url: str,
    ssl_verify: bool,
    auto_yes: bool = False,
) -> bool:
    """
    差分を表示して確認を取った上でWordPressへプッシュする。
    auto_yes=True の場合は確認をスキップ（dry-run確認済み時のみ使用）。
    戻り値: プッシュ成功なら True
    """
    try:
        old_content = _read_from_zip(filename)
    except Exception:
        old_content = ""

    if old_content == new_content:
        print(f"[theme_updater] 変更なし: {filename}")
        return True

    # diff表示
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"{filename} (現在)",
        tofile=f"{filename} (変更後)",
        lineterm="",
    ))

    print(f"\n{'='*60}")
    print(f"変更内容: {filename}")
    print(f"{'='*60}")
    added   = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    print(f"  追加: {added}行 / 削除: {removed}行\n")

    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            print(f"\033[32m{line}\033[0m", end="")
        elif line.startswith("-") and not line.startswith("---"):
            print(f"\033[31m{line}\033[0m", end="")
        else:
            print(line, end="")
    print()

    # 確認プロンプト
    if not auto_yes:
        ans = input(f"\n{filename} をWordPressにプッシュしますか？ [y/N]: ").strip().lower()
        if ans != "y":
            print("  → スキップ")
            return False

    # プッシュ
    r = requests.post(
        f"{base_url.rstrip('/')}/wp-json/premier-blog/v1/update-file",
        json={"file": filename, "content": new_content},
        headers={**auth_headers, "Content-Type": "application/json"},
        verify=ssl_verify,
        timeout=30,
    )
    if r.ok:
        _write_to_zip(filename, new_content)
        print(f"  → OK: {filename}")
        return True
    else:
        print(f"  → NG({r.status_code}): {r.text[:120]}")
        return False
