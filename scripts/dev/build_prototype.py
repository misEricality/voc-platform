"""组装高保真原型 HTML（单文件自包含：内嵌子集字体 + logo base64）

源文件：
- src/base.css / voices.css / dashboard.css   样式
- src/topbar.html                              顶栏（__LOGO__ 占位）
- src/page.html                                主区（__LEFT__ / __RIGHT__ 占位）
- src/dashboard.html                           左面板（看板图表）
- src/voices.html                              右面板（原声列表 + AI 抽屉）
- src/app.js / dashboard.js                    脚本（数据占位 /*__DATA__*/）
资源：
- data/exports/fonts/OPPO_Sans_subset.ttf      子集字体（subset_font.py 生成）
- product/logo/lynx_logo_v4a_clean.png         logo
- data/exports/prototype_voices.json           真实数据

用法：
    python scripts/dev/export_prototype_data.py
    python scripts/dev/subset_font.py
    python scripts/dev/build_prototype.py
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTO = ROOT / "product" / "prototype"
SRC = PROTO / "src"
OUT = PROTO / "voc-platform-prototype.html"
DATA_JSON = ROOT / "data" / "exports" / "prototype_voices.json"
FONT_SUBSET = ROOT / "data" / "exports" / "fonts" / "OPPO_Sans_subset.ttf"
LOGO = ROOT / "product" / "logo" / "lynx_logo_v4a_clean.png"

SRC_FILES = ["base.css", "voices.css", "dashboard.css", "topbar.html",
             "page.html", "dashboard.html", "voices.html", "app.js", "dashboard.js"]


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode("ascii")


def main() -> None:
    for name in SRC_FILES:
        if not (SRC / name).exists():
            sys.exit(f"缺少源文件: {SRC / name}")
    if not DATA_JSON.exists():
        sys.exit("缺少数据，先运行 export_prototype_data.py")
    if not FONT_SUBSET.exists():
        sys.exit("缺少子集字体，先运行 subset_font.py")
    if not LOGO.exists():
        sys.exit(f"缺少 logo: {LOGO}")

    raw = read(DATA_JSON)
    data = json.loads(raw)
    safe = raw.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    app_js = read(SRC / "app.js").replace("/*__DATA__*/ null", safe, 1)
    assert "/*__DATA__*/" not in app_js, "数据占位符未替换"
    combined_js = app_js + "\n\n" + read(SRC / "dashboard.js")

    font_b64 = b64(FONT_SUBSET)
    logo_b64 = b64(LOGO)

    topbar = read(SRC / "topbar.html").replace("__LOGO__", f"data:image/png;base64,{logo_b64}")
    page = read(SRC / "page.html") \
        .replace("__LEFT__", read(SRC / "dashboard.html").strip()) \
        .replace("__RIGHT__", read(SRC / "voices.html").strip())

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>灵听 · Lynx</title>
<style>
@font-face{{font-family:"OPPO Sans";src:url(data:font/ttf;base64,{font_b64}) format("truetype");font-weight:normal;font-display:swap;}}
</style>
<style>
{read(SRC / "base.css")}
</style>
<style>
{read(SRC / "voices.css")}
</style>
<style>
{read(SRC / "dashboard.css")}
</style>
</head>
<body>
<div class="app">
{topbar.strip()}
{page.strip()}
</div>
<footer class="footer">灵听 · Lynx | Eric</footer>
<div class="toast" id="toast">筛选条件已应用，数据已刷新</div>
<script>
{combined_js}
</script>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"voices={len(data['voices'])} opinions={len(data['opinions'])} games={len(data['games'])}")
    print(f"font={FONT_SUBSET.stat().st_size/1024:.0f}KB logo={LOGO.stat().st_size/1024:.0f}KB -> {OUT} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
