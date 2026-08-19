"""组装「游戏对比看板」HTML（单文件自包含：内嵌字体 + logo base64）

源文件：
- src/game-compare.css / .html / .js
资源：
- data/exports/fonts/OPPO_Sans_subset.ttf
- product/logo/lynx_logo_v4a_clean.png
- data/exports/game_compare_data.json

用法：
    python scripts/dev/export_game_compare.py
    python scripts/dev/build_game_compare.py
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTO = ROOT / "product" / "prototype"
SRC = PROTO / "src"
OUT = PROTO / "game-compare.html"
DATA_JSON = ROOT / "data" / "exports" / "game_compare_data.json"
FONT_SUBSET = ROOT / "data" / "exports" / "fonts" / "OPPO_Sans_subset.ttf"
LOGO = ROOT / "product" / "logo" / "lynx_logo_v4a_clean.png"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode("ascii")


def main() -> None:
    for name in ("game-compare.css", "game-compare.html", "game-compare.js"):
        if not (SRC / name).exists():
            sys.exit(f"缺少源文件: {SRC / name}")
    if not DATA_JSON.exists():
        sys.exit("缺少数据，先运行 export_game_compare.py")
    if not FONT_SUBSET.exists():
        sys.exit("缺少子集字体，先运行 subset_font.py")
    if not LOGO.exists():
        sys.exit(f"缺少 logo: {LOGO}")

    raw = read(DATA_JSON)
    data = json.loads(raw)
    safe = raw.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    js = read(SRC / "game-compare.js").replace("/*__DATA__*/ null", safe, 1)
    assert "/*__DATA__*/" not in js, "数据占位符未替换"

    font_b64 = b64(FONT_SUBSET)
    logo_b64 = b64(LOGO)
    topbar = read(SRC / "game-compare.html").replace("__LOGO__", f"data:image/png;base64,{logo_b64}")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>灵听 · Lynx · 游戏对比</title>
<style>
@font-face{{font-family:"OPPO Sans";src:url(data:font/ttf;base64,{font_b64}) format("truetype");font-weight:normal;font-display:swap;}}
</style>
<style>
{read(SRC / "game-compare.css")}
</style>
</head>
<body>
{topbar.strip()}
<script>
{js}
</script>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"games={len(data['games'])} -> {OUT} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
