"""L3.5 动态微话题聚类（阶段 2 · 首个新功能）

用法（需 torch 环境）：
    python scripts/dev/l35_cluster.py --target steam:730 --l3 外挂与作弊现象 --clusters 5

说明：
  - 手动触发：分析师框选目标 + L3 节点，本脚本做本地 bge 聚类。
  - 只读：不修改数据库、不改标注结果。
  - 输出：每个簇的样本评论 ID + 代表短语；窗口样本 <30 条时输出 low_sample_warning。
  - 簇命名：当前用「离质心最近的代表短语」作为临时名；LLM 精修命名后续接入。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from src.analyzers.embedder import get_embedder
from src.storage.db import Comment, CommentOpinion, init_db

LOW_SAMPLE_THRESHOLD = 30


def _kmeans_cosine(x: np.ndarray, k: int, iters: int = 25, seed: int = 42) -> np.ndarray:
    """极简余弦 k-means（输入已 L2 归一化，内积 = 余弦）"""
    rng = np.random.default_rng(seed)
    if k >= len(x):
        return np.arange(len(x))
    centers = x[rng.choice(len(x), k, replace=False)]
    labels = np.zeros(len(x), dtype=int)
    for _ in range(iters):
        sims = x @ centers.T
        labels = sims.argmax(axis=1)
        new_centers = np.zeros_like(centers)
        for j in range(k):
            members = x[labels == j]
            if len(members):
                c = members.mean(axis=0)
                new_centers[j] = c / (np.linalg.norm(c) + 1e-8)
        centers = new_centers
    return labels


def _fetch_rows(l3_anchor: str, target: str | None, limit: int):
    engine, SessionLocal = init_db()
    s = SessionLocal()
    stmt = (
        select(CommentOpinion, Comment)
        .join(Comment, Comment.id == CommentOpinion.comment_id)
        .where(CommentOpinion.full_path.like(f"%{l3_anchor}%"))
    )
    if target:
        stmt = stmt.where(Comment.target_id == target)
    stmt = stmt.order_by(CommentOpinion.id).limit(limit)
    rows = list(s.execute(stmt))
    s.close()
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description="L3.5 动态微话题聚类")
    p.add_argument("--l3", required=True, help="L3 名称或完整路径（如 外挂与作弊现象）")
    p.add_argument("--target", default=None, help="目标过滤（如 steam:730）")
    p.add_argument("--limit", type=int, default=500, help="最多取多少条观点")
    p.add_argument("--clusters", type=int, default=5, help="簇数量 k")
    p.add_argument("--top", type=int, default=5, help="每簇输出几条样本")
    args = p.parse_args()

    embedder = get_embedder()
    if embedder is None:
        print("[SKIP] sentence-transformers 或模型不可用。")
        sys.exit(0)

    rows = _fetch_rows(args.l3, args.target, args.limit)
    n = len(rows)
    print(f"L3 锚点：{args.l3}  目标：{args.target or '全部'}  命中观点：{n}")
    if n < LOW_SAMPLE_THRESHOLD:
        print(f"⚠️ low_sample_warning：样本仅 {n} 条（<{LOW_SAMPLE_THRESHOLD}），聚类结果仅供参考，勿输出确定性结论。")

    if n == 0:
        return

    # 用 quote（观点原声）做语义聚类
    quotes = [(row[0].quote or row[1].content or "") for row in rows]
    vecs = embedder.encode_batch(quotes)
    k = min(args.clusters, n)
    labels = _kmeans_cosine(vecs, k)

    for j in range(k):
        idx = np.where(labels == j)[0]
        # 取离质心最近的样本作为代表
        center = vecs[idx].mean(axis=0)
        center /= np.linalg.norm(center) + 1e-8
        order = idx[np.argsort(vecs[idx] @ center)[::-1]]
        top_idx = order[: args.top]
        print(f"\n簇 {j+1}（{len(idx)} 条）：")
        print(f"  临时代表短语：{quotes[top_idx[0]][:40]}")
        print(f"  sample_comment_ids: {[int(rows[i][1].id) for i in top_idx]}")
        for i in top_idx:
            print(f"    - [{rows[i][1].id}] {quotes[i][:50]}")


if __name__ == "__main__":
    main()
