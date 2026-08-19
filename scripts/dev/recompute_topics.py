"""P9 阶段1 · 兜底治理：把 comments.topic 从「整体评价」锚点下沉到具体维度。

背景：方案4 里 topic 原取自 core 观点（LLM 的 is_core 常指向"好玩/神作/垃圾"这类整体评价），
导致仪表盘 L1 主题分布被「综合与元表达」兜底桶淹没。sentiment_llm._finalize 已改为
「core 为兜底/元表达且存在具体维度观点时，topic 改用第一个具体观点；情感仍取 core」。

本脚本对存量数据做同样的下沉，且**不重跑 LLM**：只处理 topic 仍为「综合与元表达」的评论，
从其已落库的 comment_opinions 里找第一个非兜底 L1 作为新 topic。幂等。

用法：python scripts/dev/recompute_topics.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from src.storage.db import init_db, Comment, CommentOpinion
from src.analyzers.normalize import build_l3_mapping, load_hierarchy

META_L1 = "综合与元表达"


def main() -> None:
    parser = argparse.ArgumentParser(description="topic 兜底下沉（不重跑 LLM）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不落盘")
    args = parser.parse_args()

    engine, SessionLocal = init_db()
    session = SessionLocal()

    hierarchy = load_hierarchy()
    valid_l1 = set(hierarchy.keys())

    comments = list(session.execute(select(Comment)).scalars())
    # 先按 comment_id 索引意见，避免 N+1 查询
    opinions_by_cid: dict[int, list[CommentOpinion]] = {}
    for op in session.execute(
        select(CommentOpinion).order_by(CommentOpinion.comment_id, CommentOpinion.id)
    ).scalars():
        opinions_by_cid.setdefault(op.comment_id, []).append(op)

    changed = 0
    stayed_meta = 0
    new_topic_counter = Counter()
    for c in comments:
        if c.topic != META_L1:
            continue
        ops = opinions_by_cid.get(c.id, [])
        new_topic = META_L1
        for op in ops:
            l1 = op.full_path.split("/")[0]
            if l1 != META_L1 and l1 in valid_l1:
                new_topic = l1
                break
        if new_topic != META_L1:
            c.topic = new_topic
            changed += 1
            new_topic_counter[new_topic] += 1
        else:
            stayed_meta += 1

    print("=" * 70)
    print(f"topic 从「综合与元表达」下沉到具体 L1：{changed} 条")
    print(f"仍为「综合与元表达」（确实无具体维度）：{stayed_meta} 条")
    print("\n--- 下沉去向（新 topic）---")
    for t, n in new_topic_counter.most_common():
        print(f"  {t:<16} {n:>5}")

    if args.dry_run:
        session.rollback()
        print("\n[dry-run] 未落盘，已回滚")
    else:
        session.commit()
        print("\n[已提交] topic 兜底下沉完成")
    session.close()


if __name__ == "__main__":
    main()
