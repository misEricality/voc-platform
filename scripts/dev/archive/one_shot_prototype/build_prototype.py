"""
灵听 Lynx —— 原型构建脚本（主看板）

从 src/ 目录拼装单文件自包含 HTML：
- base.css / voices.css / dashboard.css   样式
- topbar.html                              顶栏
- page.html                                页面外壳（带 __LEFT__ / __RIGHT__ 占位符）
- dashboard.html                           左面板：KPI + 图表
- voices.html                              右面板：原声列表 + AI 分析
- app.js / dashboard.js                    脚本
- data/exports/prototype_voices.json       真实数据
- data/exports/fonts/OPPO_Sans_subset.ttf  子集字体
- product/logo/lynx_logo_v4a_clean.png     logo

Usage:
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


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode("ascii")


def main() -> None:
    required = [
        SRC / "base.css", SRC / "voices.css", SRC / "dashboard.css",
        SRC / "topbar.html", SRC / "page.html", SRC / "dashboard.html",
        SRC / "voices.html",
        SRC / "app.js", SRC / "dashboard.js",
        DATA_JSON, FONT_SUBSET, LOGO,
    ]
    for p in required:
        if not p.exists():
            sys.exit(f"缺少文件: {p}")

    raw = read(DATA_JSON)
    data = json.loads(raw)
    safe = raw.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    # 注入数据
    app_js = read(SRC / "app.js").replace("/*__DATA__*/ null", safe, 1)
    assert "/*__DATA__*/" not in app_js, "数据占位符未替换"

    # 拼接脚本
    full_js = app_js + "\n\n" + read(SRC / "dashboard.js")

    # 注入 logo → topbar
    logo_b64 = b64(LOGO)
    topbar_html = read(SRC / "topbar.html").replace("__LOGO__", f"data:image/png;base64,{logo_b64}")

    # page 拼装：替换 __LEFT__ / __RIGHT__ 占位符
    dashboard_html = read(SRC / "dashboard.html").strip()
    voices_html = read(SRC / "voices.html").strip()
    page_html = read(SRC / "page.html")
    page_html = page_html.replace("__LEFT__", dashboard_html)
    page_html = page_html.replace("__RIGHT__", voices_html)
    assert "__LEFT__" not in page_html, "__LEFT__ 替换失败"
    assert "__RIGHT__" not in page_html, "__RIGHT__ 替换失败"

    # 字体 base64
    font_b64 = b64(FONT_SUBSET)

    # 样式
    css = "\n".join([
        read(SRC / "base.css"),
        read(SRC / "voices.css"),
        read(SRC / "dashboard.css"),
    ])

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
{css}
</style>
</head>
<body>
<div class="app">
{topbar_html.strip()}
{page_html.strip()}
</div>
<script>
{full_js}
</script>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"voices={len(data['voices'])} games={len(data['games'])} -> {OUT} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
