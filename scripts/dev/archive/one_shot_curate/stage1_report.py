"""P9 阶段1 收口报告：兜底占比对比（topic 口径 + opinion 口径）。

跑法：python scripts/dev/stage1_report.py
对比基线：旧标签清洗后、全量重打前（综合与元表达 topic 68.6% / opinion 71.0%）。
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, func
from src.storage.db import init_db, Comment, CommentOpinion

engine, SessionLocal = init_db()
session = SessionLocal()

out: list[str] = []

def w(s=""):
    out.append(str(s))

n_comments = session.execute(select(func.count(Comment.id))).scalar()
n_opinions = session.execute(select(func.count(CommentOpinion.id))).scalar()
w("=" * 70)
w(f"comments={n_comments}  comment_opinions={n_opinions}")
w("")

w("--- comments.topic (L1) 分布 ---")
topic_c = Counter()
for (t,) in session.execute(select(Comment.topic)).all():
    topic_c[t] += 1
for t, c in topic_c.most_common():
    w(f"  {str(t):<16} {c:>5}  {c*100/n_comments:5.1f}%")
fb_topic = topic_c.get("综合与元表达", 0)
w("")
w(f">>> 兜底（综合与元表达）topic 占比 = {fb_topic*100/n_comments:.1f}%")

w("")
w("--- comment_opinions.full_path L1 分布 ---")
op_l1 = Counter()
op_fallback = Counter()
for (fp,) in session.execute(select(CommentOpinion.full_path)).all():
    l1 = fp.split("/")[0]
    op_l1[l1] += 1
    if l1 == "综合与元表达":
        op_fallback[fp] += 1
for l1, c in op_l1.most_common():
    w(f"  {l1:<16} {c:>5}  {c*100/n_opinions:5.1f}%")
fb_op = sum(op_fallback.values())
w("")
w(f">>> 兜底（综合与元表达）opinion 占比 = {fb_op*100/n_opinions:.1f}%")
w("")
w("--- 兜底内部构成（综合与元表达）---")
for fp, c in op_fallback.most_common():
    w(f"  {fp:<44} {c:>5}")

w("")
w("--- 战斗/动作/文化 词典命中后 opinion 分布（验证扩充是否生效）---")
for fp, c in sorted(op_l1.items(), key=lambda x: -x[1]):
    pass  # 已在上面打印

# 战斗/动作/文化/售价/微交易 关键词命中数（对比扩充前几乎为 0）
for l3 in ["战斗系统", "动作系统", "文化内涵与隐喻", "售价策略", "微交易·内购点"]:
    cnt = session.execute(
        select(func.count(CommentOpinion.id)).where(CommentOpinion.full_path.like(f"%{l3}"))
    ).scalar()
    w(f"  {l3}: {cnt} 条 opinion")

session.close()
print("\n".join(out))
