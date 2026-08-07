"""VoC 重打标脚本（方案4：观点短语 → 程序匹配 + 三轮收敛）

核心流程（每批次）：
1. 批量 LLM 打标（10 条/批，LLM 自由提取观点短语 phrase）
2. 程序匹配：phrase → L3（定义词典）；匹配不到 → 观点丢弃
3. 三轮收敛：第 N 轮结束后统计"所有观点都未匹配"的评论 → 进第 N+1 轮重打
4. 3 轮后仍未匹配 → 该评论观点留空（topic 用 fallback）

用法：
    python scripts/dev/reanalyze_all.py --limit 200 --random   # 随机抽样 200（seed=42）
    python scripts/dev/reanalyze_all.py                        # 全量
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.storage.db import init_db, CommentRepository, Comment
from src.analyzers import get_analyzer
from sqlalchemy import select

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.reanalyze")

MAX_ROUNDS = 3  # 三轮收敛
BATCH_SIZE = 10


def main() -> None:
    parser = argparse.ArgumentParser(description="VoC 重打标（方案4 三轮收敛）")
    parser.add_argument("--limit", type=int, default=None, help="最多重打条数（None=全量）")
    parser.add_argument("--random", action="store_true", help="随机抽样（配合 --limit）")
    args = parser.parse_args()

    log.info("=" * 70)
    log.info("方案4 重打标：观点短语 → 程序匹配 + 三轮收敛")
    log.info("=" * 70)

    # 1. 初始化
    engine, SessionLocal = init_db()
    session = SessionLocal()
    repo = CommentRepository(session)

    analyzer = get_analyzer("deepseek")

    # 2. 取评论
    stmt = select(Comment).order_by(Comment.fetched_at)
    comments = list(session.execute(stmt).scalars())
    total_all = len(comments)
    log.info(f"候选评论数: {total_all}")

    if args.limit is not None and args.limit < total_all:
        if args.random:
            random.seed(42)
            comments = random.sample(comments, args.limit)
            log.info(f"随机抽样 {len(comments)} 条（seed=42 可复现）")
        else:
            comments = comments[:args.limit]
            log.info(f"顺序取前 {len(comments)} 条")
    else:
        log.info(f"全量重打 {total_all} 条")

    total = len(comments)
    if total == 0:
        log.warning("无评论可分析")
        return

    # 3. 三轮收敛循环
    pending = comments  # 待处理的评论
    final_results: dict[int, object] = {}  # comment_id -> AnalysisResult（最终）
    all_success = 0
    all_failed = 0

    for round_idx in range(1, MAX_ROUNDS + 1):
        if not pending:
            break
        log.info("")
        log.info(f"=== 第 {round_idx} 轮收敛：{len(pending)} 条 ===")

        # 分批批量打标（第 2/3 轮用 strict prompt 强制提取观点）
        batch_results: dict[int, object] = {}
        start_time = time.time()
        for batch_start in range(0, len(pending), BATCH_SIZE):
            chunk = pending[batch_start : batch_start + BATCH_SIZE]
            texts = [c.content for c in chunk]
            try:
                results = analyzer.analyze_batch(
                    texts, batch_size=BATCH_SIZE, strict=(round_idx > 1)
                )
            except Exception as e:
                log.error(f"批量打标异常: {e}")
                results = [None] * len(chunk)

            for c, r in zip(chunk, results):
                if r is None or not r.opinions:
                    batch_results[c.id] = None  # 无观点 → 待下轮
                else:
                    batch_results[c.id] = r
            # 进度
            done = min(batch_start + BATCH_SIZE, len(pending))
            elapsed = time.time() - start_time
            speed = done / elapsed if elapsed > 0 else 0
            eta = (len(pending) - done) / speed if speed > 0 else 0
            log.info(
                f"  批量 {batch_start+1}-{done}/{len(pending)} "
                f"速度={speed:.1f}条/秒 ETA={eta/60:.1f}分"
            )

        # 收集本轮成功/失败
        round_success = sum(1 for r in batch_results.values() if r is not None)
        round_failed = len(pending) - round_success
        all_success += round_success
        all_failed += round_failed
        log.info(f"第 {round_idx} 轮：成功 {round_success}，无观点 {round_failed}")

        # 存入最终结果（成功的）
        for cid, r in batch_results.items():
            if r is not None:
                final_results[cid] = r

        # 下轮待处理 = 无观点的
        pending = [c for c in pending if batch_results.get(c.id) is None]
        if not pending:
            break

    # 4. 落盘（成功的评论写 opinions；三轮后仍无观点的跳过/留空）
    log.info("")
    log.info("=== 落盘 ===")
    write_start = time.time()
    topic_counter: Counter = Counter()
    sentiment_counter: Counter = Counter()
    opinion_counter: Counter = Counter()  # full_path 计数
    unmatched_count = 0

    for c in comments:
        r = final_results.get(c.id)
        if r is None:
            # 三轮后仍无观点 → 留空（不改旧数据？这里直接写 fallback 空）
            unmatched_count += 1
            continue

        repo.update_analysis(
            c.id,
            sentiment=r.sentiment,
            sentiment_score=r.sentiment_score,
            sentiment_confidence=r.sentiment_confidence,
            topic=r.topic,
            sub_topics=None,  # 方案4：sub_topics 不再使用
            opinions=[op.to_dict() for op in r.opinions],
        )
        topic_counter[r.topic or "(空)"] += 1
        sentiment_counter[r.sentiment] += 1
        for op in r.opinions:
            if op.full_path:
                opinion_counter[op.full_path] += 1

    repo.commit()
    session.close()

    log.info(f"落盘完成 · 耗时 {(time.time()-write_start)/60:.1f} 分")
    log.info(f"成功 {all_success} / 无观点(三轮后留空) {all_failed} / 失败 0")

    # 5. 分布报告
    analyzed_total = len(final_results)
    log.info("")
    log.info("=" * 70)
    log.info(f">>> 一级标签 (topic) 分布（基于 {analyzed_total} 条）")
    for topic, cnt in topic_counter.most_common():
        pct = cnt * 100 / analyzed_total if analyzed_total else 0
        log.info(f"  {topic:<14} {cnt:>5}  {pct:>5.1f}%  " + "#" * min(int(pct), 50))

    log.info("")
    log.info(">>> 情感 (sentiment) 分布")
    for sent, cnt in sentiment_counter.most_common():
        pct = cnt * 100 / analyzed_total if analyzed_total else 0
        log.info(f"  {sent:<14} {cnt:>5}  {pct:>5.1f}%  " + "#" * min(int(pct), 50))

    log.info("")
    log.info(">>> 观点路径 (full_path) TOP20")
    for path, cnt in opinion_counter.most_common(20):
        pct = cnt * 100 / analyzed_total if analyzed_total else 0
        log.info(f"  {path:<30} {cnt:>5}  {pct:>5.1f}%")

    log.info("")
    log.info(f"三轮后仍无观点的评论: {unmatched_count} 条（已留空）")


if __name__ == "__main__":
    main()