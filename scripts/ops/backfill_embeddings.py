"""评论 embedding 回填 / 全量重算脚本

业务背景：
  向量化是数据处理新增的一步（2026-08-11）。存量 2067 条评论没有向量，
  需本脚本回填；未来换了 embedding 模型，也需本脚本全量重算。

用法：
  # 增量回填：只补缺失向量（断点续跑，可反复执行）
  python scripts/ops/backfill_embeddings.py --limit 100

  # 全量重算（换模型 / 清理）：清空旧向量后重建，单事务原子切换
  python scripts/ops/backfill_embeddings.py --force --limit 100

  # 按平台/目标过滤
  python scripts/ops/backfill_embeddings.py --platform steam
  python scripts/ops/backfill_embeddings.py --target steam:730

设计要点：
  - 衍生数据：向量可由 comments.content 随时全量重建，换模型 = --force 重算，不保留旧向量
  - 原子切换：全部编码到内存（N×512×4B，1 万条约 20MB 可忽略）→ 单事务 DELETE+INSERT；
    中途失败 SQLite 自动回滚，旧向量保留 → 任意时刻表内只有完整的一种模型
  - 单空间：--force 后表内只有当前 --model 一种模型（默认取 embedder 模型）
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from dotenv import load_dotenv

# 项目根目录注入：scripts/ops/backfill_embeddings.py → ../../../ （即 voc_platform/）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
load_dotenv()

from src.analyzers.embedder import get_embedder
from src.storage.db import Comment, CommentEmbedding, CommentRepository, init_db, select

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.backfill_embeddings")


def _target_ids(
    repo: CommentRepository,
    platform: str | None,
    target: str | None,
    force: bool,
) -> list[int]:
    """待处理评论 id：--force 时取全部（按过滤条件），否则取缺失向量的"""
    stmt = select(Comment.id)
    if platform:
        stmt = stmt.where(Comment.platform == platform)
    if target:
        stmt = stmt.where(Comment.target_id == target)
    all_ids = [r for (r,) in repo.session.execute(stmt)]

    if not force:
        have = {cid for (cid,) in repo.session.execute(select(CommentEmbedding.comment_id))}
        all_ids = [cid for cid in all_ids if cid not in have]
    return all_ids


def main():
    parser = argparse.ArgumentParser(description="评论 embedding 回填 / 全量重算")
    parser.add_argument("--limit", type=int, default=0, help="最多处理条数（0=全部）")
    parser.add_argument("--force", action="store_true", help="全量重算（清空旧向量后重建）")
    parser.add_argument("--platform", default=None, help="平台过滤（steam）")
    parser.add_argument("--target", default=None, help="目标过滤（如 steam:730）")
    parser.add_argument("--batch-size", type=int, default=64, help="编码批大小")
    args = parser.parse_args()

    emb = get_embedder()
    if emb is None:
        log.error("embedder 不可用：未安装 sentence-transformers 或模型加载失败")
        sys.exit(1)

    engine, SessionLocal = init_db()
    session = SessionLocal()
    repo = CommentRepository(session)

    # 防线 1：非 force 时若表内已有其他模型，拒绝增量混写（必须先 --force）
    existing = repo.embedding_models_in_use()
    if existing and set(existing) != {emb.model_name} and not args.force:
        log.error(
            f"表内已有其他模型 {existing}，当前 {emb.model_name}；"
            f"请先执行 --force 全量重算，禁止混合空间"
        )
        sys.exit(2)

    ids = _target_ids(repo, args.platform, args.target, args.force)
    if args.limit > 0:
        ids = ids[: args.limit]
    if not ids:
        log.info("没有需要处理的评论（无缺失 / 已全部向量化）")
        return

    mode = "全量重算" if args.force else "增量回填"
    log.info(f"[{mode}] 待处理 {len(ids)} 条（模型 {emb.model_name}，dim={emb.dim}）")

    # 1. 分批编码到内存（不落库）
    comments = {c.id: c for c in repo.get_comments_by_ids(ids)}
    vecs: list = []
    t0 = time.time()
    for start in range(0, len(ids), args.batch_size):
        chunk = ids[start : start + args.batch_size]
        texts = [comments[cid].content for cid in chunk if cid in comments]
        if texts:
            vecs.extend(emb.encode_batch(texts, batch_size=args.batch_size))
    log.info(f"  编码完成 {len(vecs)} 条，耗时 {time.time() - t0:.1f}s")

    # 2. 原子写入：force=单事务删插；否则逐条 upsert
    done_ids = [cid for cid in ids if cid in comments][: len(vecs)]
    if args.force:
        n = repo.replace_all_embeddings(done_ids, vecs, emb.model_name, emb.dim)
    else:
        n = repo.save_embeddings(done_ids, vecs, emb.model_name, emb.dim)
    log.info(f"  落库 {n} 条，当前表内共 {repo.count_embeddings()} 条向量（模型 {repo.embedding_models_in_use()}）")

    session.close()


if __name__ == "__main__":
    main()
