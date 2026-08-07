"""组装高保真原型 HTML

以 v1 备份为骨架（保留全局样式与顶栏），注入：
- product/prototype/src/voices.css    原声列表 v2 样式（筛选器组件）
- product/prototype/src/dashboard.css 数据看板 v2 样式
- product/prototype/src/dashboard.html 数据看板区结构
- product/prototype/src/voices.html   原声列表区结构
- product/prototype/src/drawer.html   详情抽屉 + 悬浮全文容器
- product/prototype/src/app.js        共享脚本（占位符注入真实数据）
- product/prototype/src/dashboard.js  看板渲染（拼接至 app.js 之后）
- data/exports/prototype_voices.json  真实数据（由 export_prototype_data.py 生成）

用法：
    python scripts/dev/export_prototype_data.py   # 先生成数据
    python scripts/dev/build_prototype.py         # 再组装原型
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTO = ROOT / "product" / "prototype"
BACKUP = PROTO / "voc-platform-prototype.v1.bak.html"
OUT = PROTO / "voc-platform-prototype.html"
DATA_JSON = ROOT / "data" / "exports" / "prototype_voices.json"

SRC = [
    "voices.css", "dashboard.css", "dashboard.html", "voices.html",
    "drawer.html", "app.js", "dashboard.js",
]


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def main() -> None:
    for name in SRC:
        p = PROTO / "src" / name
        if not p.exists():
            sys.exit(f"缺少文件: {p}")
    if not BACKUP.exists():
        sys.exit(f"缺少 v1 备份: {BACKUP}")

    html = read(BACKUP)

    # 1. 注入 v2 样式（原声列表 + 数据看板）
    v2css = "<style>\n" + read(PROTO / "src" / "voices.css") + "\n" \
        + read(PROTO / "src" / "dashboard.css") + "\n</style>"
    html = html.replace("</head>", v2css + "\n</head>")

    # 2. 替换数据看板 section
    dash_html = read(PROTO / "src" / "dashboard.html").strip()
    html, n = re.subn(r'<section id="dashboard"[^>]*>.*?</section>', lambda m: dash_html, html, flags=re.S)
    assert n == 1, "dashboard section 替换失败"

    # 3. 替换原声列表 section
    voices_html = read(PROTO / "src" / "voices.html").strip()
    html, n = re.subn(r'<section id="voices"[^>]*>.*?</section>', lambda m: voices_html, html, flags=re.S)
    assert n == 1, "voices section 替换失败"

    # 4. 替换旧抽屉为 v2 抽屉 + 悬浮容器
    drawer_html = read(PROTO / "src" / "drawer.html").strip()
    html, n = re.subn(r'<div class="drawer-mask".*?(?=<div class="toast")', lambda m: drawer_html, html, flags=re.S)
    assert n == 1, "drawer 替换失败"

    # 5. 注入数据并拼接脚本（app.js -> dashboard.js）
    raw = read(DATA_JSON)
    data = json.loads(raw)  # 校验 JSON 合法
    safe = raw.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    app_js = read(PROTO / "src" / "app.js").replace("/*__DATA__*/ null", safe, 1)
    assert "/*__DATA__*/" not in app_js, "数据占位符未替换"
    combined_js = app_js + "\n\n" + read(PROTO / "src" / "dashboard.js")
    html, n = re.subn(r"<script>.*?</script>", lambda m: "<script>\n" + combined_js + "\n</script>", html, flags=re.S)
    assert n == 1, "script 替换失败"

    OUT.write_text(html, encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"voices={len(data['voices'])} games={len(data['games'])} -> {OUT} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
