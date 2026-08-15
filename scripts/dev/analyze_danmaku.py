"""B 站弹幕词典打标 + 时间窗情绪聚合（2026-08-13）

规格：弹幕不进 LLM 打标链路（成本红线），用词典匹配（复用 normalize.match_l3）+ 时间窗聚合。

输出：
1. 弹幕 L1/L2/观点路径分布（词典匹配结果）
2. 时间窗聚合：每 30 秒窗口的弹幕量 + 标签 TOP + 情绪倾向
3. 情绪粗判：OVERALL_PRAISE / OVERALL_CRITICIZE 词表命中 → positive/negative
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.analyzers.normalize import (
    build_keyword_index,
    build_l3_mapping,
    load_definitions,
    load_hierarchy,
    match_l3,
    map_l3_to_path,
    OVERALL_PRAISE,
    OVERALL_CRITICIZE,
)

WINDOW = 30  # 时间窗（秒）


def main() -> None:
    # 词典加载
    h = load_hierarchy()
    mapping = build_l3_mapping(h)
    defs = load_definitions()
    kw_idx = build_keyword_index(defs)
    print(f"词典: L3 定义 {len(defs)} 个")

    import sqlite3
    conn = sqlite3.connect("data/voc.db")
    rows = conn.execute(
        "SELECT content, progress, user_hash FROM danmaku ORDER BY progress"
    ).fetchall()
    print(f"弹幕总数: {len(rows)}")

    l1_counter: Counter = Counter()
    l2_counter: Counter = Counter()
    path_counter: Counter = Counter()
    unmatched = 0
    matched = 0

    # 弹幕级标注（词典匹配）
    dm_labels: dict[int, str | None] = {}  # row_idx -> full_path
    for i, (content, progress, _) in enumerate(rows):
        l3 = match_l3(content, kw_idx, defs)
        if l3:
            path = map_l3_to_path(l3, mapping)
            dm_labels[i] = path
            if path:
                l1_counter[path.split("/")[0]] += 1
                l2_counter[path.split("/")[1] if len(path.split("/")) > 1 else path] += 1
                path_counter[path] += 1
                matched += 1
        else:
            dm_labels[i] = None
            unmatched += 1

    total = len(rows)
    print(f"\n=== 弹幕词典匹配结果 ===")
    print(f"匹配到标签: {matched} ({matched*100/total:.1f}%) | 未匹配: {unmatched} ({unmatched*100/total:.1f}%)")

    print(f"\n--- L1 分布 ---")
    for k, v in l1_counter.most_common():
        print(f"  {k:<12} {v:>5}  {v*100/matched:.1f}%" if matched else "")

    print(f"\n--- L2 分布 TOP 15 ---")
    for k, v in l2_counter.most_common(15):
        print(f"  {k:<12} {v:>5}  {v*100/matched:.1f}%")

    print(f"\n--- 观点路径 TOP 15 ---")
    for k, v in path_counter.most_common(15):
        print(f"  {k:<32} {v:>5}")

    # 时间窗聚合
    print(f"\n=== 时间窗聚合（每 {WINDOW} 秒）===")
    max_progress = max(r[1] for r in rows) if rows else 0
    n_windows = max_progress // WINDOW + 1
    print(f"{'窗口':<10} {'弹幕数':>6} {'TOP标签':<28} {'正向':>4} {'负向':>4}")
    for w in range(n_windows):
        start = w * WINDOW
        end = start + WINDOW
        win_rows = [(i, r) for i, r in enumerate(rows) if start <= r[1] < end]
        if not win_rows:
            continue
        win_counter: Counter = Counter()
        pos = neg = 0
        for i, (content, _, _) in win_rows:
            if dm_labels[i]:
                win_counter[dm_labels[i]] += 1
            # 情绪粗判：夸词优先，其次贬词
            # （旧版按 OVERALL_POSITIVE.index()<18 判正负，但词表夸贬位置交错，
            #   6 个贬词被误判正、10 个夸词被误判负；2026-08-15 修复为独立列表）
            if any(kw in content for kw in OVERALL_PRAISE):
                pos += 1
            elif any(kw in content for kw in OVERALL_CRITICIZE):
                neg += 1
        top = win_counter.most_common(1)
        top_str = top[0][0].split("/")[-1] if top else "-"
        top_cnt = f"({top[0][1]})" if top else ""
        print(f"  {start:>3}-{end:<4}s  {len(win_rows):>6}  {top_str+top_cnt:<28} {pos:>4} {neg:>4}")

    conn.close()


if __name__ == "__main__":
    main()
