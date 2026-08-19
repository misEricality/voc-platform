"""挖掘"总体体验评价"兜底观点中的词典缺口候选（方案4 词典维护工具）

用途：每次有新数据落库、或想优化 L3 匹配时，扫一遍兜底桶，挑出
"疑似有具体话题、但当前词典没覆盖"的高频短语，供人工精编入库。

背景（2026-08-18，实测 data/voc.db 9308 条）：
- 兜底桶大头是"合理整体评价"（好玩×858 / 垃圾游戏×88 / 神作×77…），这类不是缺口；
- 真正的词典缺口在"长尾具体话题短语"（如"韧性低，受击硬直长""黑猴的怪粪招不少"）；
- 本脚本自动过滤"合理整体评价"，只列出疑似缺口候选，按出现次数排序，方便按影响挑词。

工作流（沉淀为固定步骤）：
    新数据落库 → 跑本脚本 → 人工挑词 → 加进 l3_definitions.yaml / normalize.py
    → 跑 tests/test_golden_match.py（黄金集回归门禁）→ 完成

用法：
    python scripts/dev/mine_fallback_candidates.py                    # 默认：全部平台，列出候选
    python scripts/dev/mine_fallback_candidates.py --min-count 2 --limit 100
    python scripts/dev/mine_fallback_candidates.py --platform steam --out candidates.md

输出分类：
    [候选]  疑似具体话题漏网，可考虑加词（重点看）
    [已救]  当前词典已能匹配到具体 L3（无需处理）
    [整体]  含整体褒贬/推荐词，本就该兜底（忽略）
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.analyzers.normalize import (  # noqa: E402
    OVERALL_CRITICIZE,
    OVERALL_PRAISE,
    RECOMMEND_WORDS,
    build_keyword_index,
    load_definitions,
    match_l3,
)

FALLBACK_LIKE = "综合与元表达/整体印象/总体体验评价%"
FALLBACK_RETURNS = (None, "总体体验评价", "综合推荐度")

OVERALL_ALL = (*OVERALL_PRAISE, *OVERALL_CRITICIZE, *RECOMMEND_WORDS)


def _is_overall(phrase: str) -> bool:
    """是否含"整体褒贬/推荐"词（→ 本就该兜底，不是缺口）；英文词大小写不敏感"""
    pl = phrase.lower()
    return any(kw and kw.lower() in pl for kw in OVERALL_ALL)


def _is_noise(phrase: str) -> bool:
    """明显噪音：纯数字 / 纯拼音·英文（含带空格）/ 单字符重复（无可入库价值）"""
    stripped = phrase.strip()
    if not stripped:
        return True
    if stripped.isdigit():                       # 666666 / 111111
        return True
    no_space = stripped.replace(" ", "")
    if no_space and all(c.isascii() and c.isalpha() for c in no_space):
        return True                             # haowan / hao wan / GOODGAME
    return len(set(stripped)) == 1               # ♥♥♥♥ / 1111


def _fetch_fallback_rows(db: str, platform: str) -> list[tuple[str, int]]:
    conn = sqlite3.connect(db)
    q = (
        "select quote, count(*) from comment_opinions "
        "where full_path like ? "
    )
    params: list = [FALLBACK_LIKE]
    if platform != "all":
        q += "and comment_id in (select id from comments where platform=?) "
        params.append(platform)
    q += "group by quote"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="挖掘兜底观点中的词典缺口候选")
    ap.add_argument("--db", default="data/voc.db", help="SQLite 数据库路径")
    ap.add_argument("--limit", type=int, default=200, help="最多列出候选数")
    ap.add_argument("--min-count", type=int, default=1, help="短语最少出现次数")
    ap.add_argument("--min-len", type=int, default=6, help="短语最少字符数（过滤短整体评价）")
    ap.add_argument("--platform", choices=["steam", "bilibili", "all"], default="all")
    ap.add_argument("--out", help="将候选写入 Markdown 文件（可选）")
    args = ap.parse_args()

    defs = load_definitions()
    kidx = build_keyword_index(defs)
    rows = _fetch_fallback_rows(args.db, args.platform)

    candidates: list[tuple[int, int, str]] = []  # (count, len, phrase)
    rescued: list[tuple[int, str, str]] = []      # (count, phrase, l3)
    overall_cnt = 0
    noise_cnt = 0
    total_cnt = 0
    for phrase, cnt in rows:
        total_cnt += cnt
        if len(phrase) < args.min_len:
            continue
        got = match_l3(phrase, kidx, defs)
        if got not in FALLBACK_RETURNS:
            rescued.append((cnt, phrase, got))
            continue
        if _is_overall(phrase):
            overall_cnt += cnt
            continue
        if _is_noise(phrase):
            noise_cnt += cnt
            continue
        if cnt >= args.min_count:
            candidates.append((cnt, len(phrase), phrase))

    candidates.sort(key=lambda x: (-x[0], -x[1]))
    rescued.sort(key=lambda x: -x[0])
    cand_total = sum(c for c, _, _ in candidates)

    # ---- 控制台输出 ----
    print(f"兜底观点总计: {total_cnt} 条")
    print(f"  合理整体评价（忽略）: {overall_cnt} 条")
    print(f"  噪音（数字/拼音/重复符，忽略）: {noise_cnt} 条")
    print(f"  当前词典已救出（无需处理）: {len(rescued)} 个短语 / {sum(c for c,_,_ in rescued)} 次")
    print(f"  疑似缺口候选: {len(candidates)} 个短语 / {cand_total} 次  ⬅ 重点看这里")
    print()
    print(f"{'次数':>4} {'长':>2}  {'短语':<48}")
    print("-" * 60)
    shown = candidates[: args.limit]
    for cnt, ln, phrase in shown:
        print(f"{cnt:>4} {ln:>2}  {phrase}")
    if len(candidates) > args.limit:
        print(f"... 共 {len(candidates)} 个候选，仅显示前 {args.limit} 个（可 --limit 调大）")

    # ---- 可选写出 Markdown ----
    if args.out:
        lines = [
            f"# 兜底词典缺口候选（{datetime.now():%Y-%m-%d} · platform={args.platform}）",
            "",
            f"- 兜底观点总计: {total_cnt} 条",
            f"- 合理整体评价（忽略）: {overall_cnt} 条",
            f"- 噪音（数字/拼音/重复符，忽略）: {noise_cnt} 条",
            f"- 当前词典已救出: {len(rescued)} 短语 / {sum(c for c,_,_ in rescued)} 次",
            f"- **疑似缺口候选: {len(candidates)} 短语 / {cand_total} 次**",
            "",
            "## 候选清单（按出现次数排序，供人工精编入库）",
            "",
            "| # | 次数 | 长度 | 短语 | 拟加词 | 目标 L3 |",
            "|---|----:|----:|------|--------|---------|",
        ]
        for i, (cnt, ln, phrase) in enumerate(shown, 1):
            lines.append(f"| {i} | {cnt} | {ln} | {phrase} |  |  |")
        Path(args.out).write_text("\n".join(lines), encoding="utf-8")
        print(f"\n候选清单已写入: {args.out}")


if __name__ == "__main__":
    main()
