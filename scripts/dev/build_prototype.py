"""组装高保真原型 HTML（完全自组装，不依赖历史骨架）

由源文件拼接出完整单文件原型：
- product/prototype/src/base.css       全局基础样式
- product/prototype/src/voices.css     筛选器 / 列表 / 抽屉样式
- product/prototype/src/dashboard.css  看板图表样式
- product/prototype/src/topbar.html    顶栏（品牌「灵听 Lynx」）
- product/prototype/src/dashboard.html 数据看板区
- product/prototype/src/voices.html    原声列表区
- product/prototype/src/drawer.html    详情抽屉 + 悬浮全文容器
- product/prototype/src/app.js         共享脚本（占位符注入真实数据）
- product/prototype/src/dashboard.js   看板渲染（拼接至 app.js 之后）
- data/exports/prototype_voices.json   真实数据（由 export_prototype_data.py 生成）

用法：
    python scripts/dev/export_prototype_data.py   # 先生成数据
    python scripts/dev/build_prototype.py         # 再组装原型
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTO = ROOT / "product" / "prototype"
SRC = PROTO / "src"
OUT = PROTO / "voc-platform-prototype.html"
DATA_JSON = ROOT / "data" / "exports" / "prototype_voices.json"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def main() -> None:
    for name in ("base.css", "voices.css", "dashboard.css", "topbar.html",
                 "dashboard.html", "voices.html", "drawer.html", "app.js", "dashboard.js"):
        if not (SRC / name).exists():
            sys.exit(f"缺少源文件: {SRC / name}")

    raw = read(DATA_JSON)
    data = json.loads(raw)  # 校验 JSON 合法
    safe = raw.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    app_js = read(SRC / "app.js").replace("/*__DATA__*/ null", safe, 1)
    assert "/*__DATA__*/" not in app_js, "数据占位符未替换"
    combined_js = app_js + "\n\n" + read(SRC / "dashboard.js")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>灵听 Lynx · 洞察平台</title>
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
{read(SRC / "topbar.html").strip()}
<main class="main">
{read(SRC / "dashboard.html").strip()}
{read(SRC / "voices.html").strip()}
</main></div>
{read(SRC / "drawer.html").strip()}
<div class="toast" id="toast">筛选条件已应用，数据已刷新</div>
<script>
{combined_js}
</script>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"voices={len(data['voices'])} opinions={len(data['opinions'])} games={len(data['games'])} -> {OUT} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
