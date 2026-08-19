"""重匹配 opinions.full_path：用当前词典重跑 match_l3（不重跑 LLM）。

背景：词典扩充后（config/topics/l3_definitions.yaml / src/analyzers/normalize.py），
库里已有观点的 full_path 仍是旧词典打的。本脚本对 comment_opinions.quote 重跑
match_l3 → map_l3_to_path，把「兜底 → 具体」等变化写回 full_path。幂等、¥0、秒级。

用法：
    python scripts/dev/rematch_opinions.py --dry-run   # 只统计，不落盘
    python scripts/dev/rematch_opinions.py             # 应用

说明：只改 full_path（标签路径），不动 sentiment / quote / 其他字段。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import select  # noqa: E402

from src.storage.db import init_db, CommentOpinion  # noqa: E402
from src.analyzers.normalize import (  # noqa: E402
    build_keyword_index,
    build_l3_mapping,
    load_definitions,
    load_hierarchy,
    map_l3_to_path,
    match_l3,
)

META_L1 = "综合与元表达"


def main() -> None:
    parser = argparse.ArgumentParser(description="重匹配 opinions.full_path（不重跑 LLM）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不落盘")
    args = parser.parse_args()

    defs = load_definitions()
    kidx = build_keyword_index(defs)
    mapping = build_l3_mapping(load_hierarchy())

    engine, SessionLocal = init_db()
    session = SessionLocal()
    ops = list(session.execute(select(CommentOpinion)).scalars())

    rescued = Counter()        # 兜底 → 具体（老 L1=综合与元表达 → 新 L1）
    specific_changed = Counter()  # 具体 → 具体（L3 变化，需人工复核）
    changed = 0
    for op in ops:
        phrase = (op.quote or "").strip()
        if not phrase:
            continue
        new_l3 = match_l3(phrase, kidx, defs)
        new_path = map_l3_to_path(new_l3, mapping) if new_l3 else None
        if not new_path or new_path == op.full_path:
            continue
        old_l1 = (op.full_path or "").split("/")[0]
        new_l1 = new_path.split("/")[0]
        if old_l1 == META_L1 and new_l1 != META_L1:
            # 兜底 → 具体：唯一默认写回的类型（纯收益）
            rescued[new_path] += 1
            if not args.dry_run:
                op.full_path = new_path
        else:
            # 具体 → 具体 / 兜底 → 其他兜底子类：仅报告，不落盘（避免回退）
            specific_changed[f"{op.full_path} -> {new_path}"] += 1
        changed += 1

    print("=" * 70)
    print(f"重匹配变化总数: {changed} 条")
    print(f"  兜底 → 具体（本次写回，纯收益）: {sum(rescued.values())} 条")
    print(f"  具体 → 具体 / 兜底→其他兜底子类（仅报告，不落盘）: {sum(specific_changed.values())} 条")
    print()
    if rescued:
        print("--- 兜底救出去向（新 full_path）---")
        for fp, n in rescued.most_common():
            print(f"  {fp:<44} {n:>5}")
    if specific_changed:
        print("--- 具体→具体 变化（复核）---")
        for fp, n in specific_changed.most_common(30):
            print(f"  {fp:<70} {n:>3}")

    if args.dry_run:
        session.rollback()
        print("\n[dry-run] 未落盘，已回滚")
    else:
        session.commit()
        print("\n[已提交] full_path 重匹配完成")
    session.close()


if __name__ == "__main__":
    main()
