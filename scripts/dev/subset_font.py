"""字体子集化：OPPO Sans 4.0 → 子集 TTF（仅保留原型实际用到的字符）

收集字符来源：
- src 目录全部 .html/.css/.js（界面文案）
- 数据 JSON（评论内容 / 观点引用 / 标签路径）
- ASCII 可打印字符 + 常用中文标点

输出：data/exports/fonts/OPPO_Sans_subset.ttf（供 build_prototype.py 转 base64 内嵌）

用法：python scripts/dev/subset_font.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[2]
FONT = Path("D:/fonts/OPPO_Sans_4.0/OPPO_Sans_4.0/OPPO Sans 4.0.ttf")
SRC_DIR = ROOT / "product" / "prototype" / "src"
DATA_JSON = ROOT / "data" / "exports" / "prototype_voices.json"
OUT_DIR = ROOT / "data" / "exports" / "fonts"
OUT = OUT_DIR / "OPPO_Sans_subset.ttf"

# 常用中文标点 + 特殊字符（补充 ASCII 之外的）
EXTRA_CHARS = "　…—·×“”‘’、。《》【】（）￥…—～·%‰±×÷"


def collect_text() -> str:
    chars: set[str] = set()
    chars.update(chr(c) for c in range(0x20, 0x7F))  # ASCII 可打印
    chars.update(EXTRA_CHARS)
    for p in sorted(SRC_DIR.glob("*")):
        if p.suffix in (".html", ".css", ".js"):
            chars.update(p.read_text(encoding="utf-8"))
    chars.update(DATA_JSON.read_text(encoding="utf-8"))
    return "".join(chars)


def main() -> None:
    if not FONT.exists():
        sys.exit(f"字体不存在: {FONT}")
    text = collect_text()
    font = TTFont(str(FONT))
    options = Options()
    subsetter = Subsetter(options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    font.save(str(OUT))
    size_kb = OUT.stat().st_size / 1024
    print(f"收集字符 {len(set(text))} 个 -> {OUT} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
