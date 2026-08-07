"""收敛循环重打标脚本（2026-08-05 一次性任务）

设计要点：
- 找出"越界 sub_topics"或"topic=其他"的评论 → 用加强版 prompt 重打
- 每轮结束检查越界数，直到 0 或达到最大轮数
- 每轮结束输出进度报告
- 完成后调用 PowerShell 弹窗通知

业务背景：
- 首轮打标 5 处 sub_topics 越界 + 691 条 topic=其他（命中率 33.4%）
- prompt 已加强硬约束（config/prompts/sentiment_user.txt）
- 收敛目标：sub_topics 越界为 0
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import yaml

from dotenv import load_dotenv

load_dotenv()

from src.storage.db import init_db, CommentRepository
from src.analyzers import get_analyzer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.outliers")

MAX_ROUNDS = 3  # 最大收敛轮数


def load_valid_labels() -> tuple[set[str], set[str]]:
    """加载合法的 L1 和 L2 标签集合"""
    path = Path("config/topics/gaming.yaml")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    all_l1 = set(cfg.get("primary", []))
    all_l2: set[str] = set()
    for l1, subs in cfg.get("hierarchy", {}).items():
        if isinstance(subs, dict):
            all_l2.update(subs.keys())
    return all_l1, all_l2


def is_outlier(topic: str, sub_topics: list[str], all_l1: set, all_l2: set) -> bool:
    """判断一条评论是否越界（topic 不在 L1 / sub_topics 不在 L2）"""
    if topic not in all_l1:
        return True
    for st in sub_topics or []:
        if st not in all_l2:
            return True
    return False


def fetch_outlier_ids(session, all_l1: set, all_l2: set, only_sub: bool = False) -> list[int]:
    """从 DB 找出所有越界评论的 id

    Args:
        only_sub: True 时只看 sub_topics 越界；False 时同时看 topic=其他
    """
    from sqlalchemy import select
    from src.storage.db import Comment

    stmt = select(Comment).where(Comment.analyzed_at.is_not(None))
    rows = list(session.execute(stmt).scalars())
    outlier_ids = []
    for c in rows:
        try:
            subs = json.loads(c.sub_topics) if c.sub_topics else []
        except Exception:
            subs = []
        # 子标签越界 → 总是纳入
        sub_bad = any(s not in all_l2 for s in subs)
        # topic=其他 → 仅在 only_sub=False 时纳入
        topic_other = c.topic == "其他"
        if sub_bad or (not only_sub and topic_other):
            outlier_ids.append(c.id)
    return outlier_ids


def notify_user(title: str, message: str, stats: dict | None = None) -> None:
    """完成后通知用户（多 fallback 方案）

    优先级：
    1. PowerShell MessageBox（沙箱可能拦截）
    2. winsound.MessageBeep（系统声音）
    3. 写 data/analysis_done.flag + JSON（保底可靠，AI 下次会话读取）
    """
    # Fallback 1: PowerShell MessageBox
    try:
        ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show(
    "{message}",
    "{title}",
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Information
)
"""
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            timeout=10,
        )
        log.info(f"[notify] PowerShell MessageBox 已尝试")
    except Exception as e:
        log.warning(f"[notify] PowerShell 失败: {e}")

    # Fallback 2: winsound 系统声音（不一定听得到，但无副作用）
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        log.info(f"[notify] winsound.MessageBeep 已响")
    except Exception as e:
        log.warning(f"[notify] winsound 失败: {e}")

    # Fallback 3: 写文件标志 + JSON 统计（保底）
    try:
        flag_path = Path("data/analysis_done.flag")
        import json as _json
        payload = {
            "title": title,
            "message": message,
            "stats": stats or {},
            "timestamp": datetime.utcnow().isoformat(),
        }
        flag_path.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"[notify] 已写标志文件: {flag_path}")
    except Exception as e:
        log.warning(f"[notify] 写文件失败: {e}")


def main() -> None:
    log.info("=" * 70)
    log.info("收敛循环重打标 · 目标：消除 sub_topics 越界 + topic=其他 兜底过多")
    log.info("=" * 70)

    all_l1, all_l2 = load_valid_labels()
    log.info(f"合法 L1 数: {len(all_l1)} | 合法 L2 数: {len(all_l2)}")

    engine, SessionLocal = init_db()
    session = SessionLocal()
    repo = CommentRepository(session)

    analyzer = get_analyzer("deepseek")

    for round_idx in range(1, MAX_ROUNDS + 1):
        log.info("")
        log.info("=" * 70)
        log.info(f"=== 第 {round_idx} 轮收敛 ===")
        log.info("=" * 70)

        # 取越界 + topic=其他 的评论
        # 第 1 轮：包含 topic=其他 + sub_topics 越界
        # 后续轮：只盯 sub_topics 越界（topic=其他 可能合理）
        only_sub = round_idx > 1
        target_ids = fetch_outlier_ids(session, all_l1, all_l2, only_sub=only_sub)

        if not target_ids:
            log.info(f"✅ 第 {round_idx} 轮：无越界评论，收敛完成")
            break

        log.info(f"待重打评论数: {len(target_ids)}")

        # 拉出这些评论
        from sqlalchemy import select
        from src.storage.db import Comment

        target_comments = list(
            session.execute(select(Comment).where(Comment.id.in_(target_ids))).scalars()
        )

        success = 0
        failed = 0
        new_outlier_sub = 0
        new_outlier_topic = 0
        topic_changes: Counter = Counter()
        start = time.time()

        for i, c in enumerate(target_comments, 1):
            try:
                result = analyzer.analyze(
                    c.content,
                    context={"platform": c.platform, "target_id": c.target_id},
                )
                repo.update_analysis(
                    c.id,
                    sentiment=result.sentiment,
                    sentiment_score=result.sentiment_score,
                    sentiment_confidence=result.sentiment_confidence,
                    topic=result.topic,
                    sub_topics=result.sub_topics,
                    opinions=[op.to_dict() for op in result.opinions],
                    valid_l1_labels=all_l1,
                    valid_l2_labels=all_l2,
                )
                # 校验新结果
                if result.topic not in all_l1:
                    new_outlier_topic += 1
                for st in result.sub_topics or []:
                    if st not in all_l2:
                        new_outlier_sub += 1
                if result.topic and result.topic != c.topic:
                    topic_changes[(c.topic or "?", result.topic)] += 1
                success += 1
            except Exception as e:
                failed += 1
                log.warning(f"id={c.id} 分析失败: {e}")

            if i % 50 == 0 or i == len(target_comments):
                repo.commit()
                elapsed = time.time() - start
                speed = i / elapsed if elapsed > 0 else 0
                eta = (len(target_comments) - i) / speed if speed > 0 else 0
                log.info(
                    f"  进度 {i}/{len(target_comments)} "
                    f"成功={success} 失败={failed} "
                    f"新 sub_越界={new_outlier_sub} 新 topic_越界={new_outlier_topic} "
                    f"速度={speed:.1f}条/秒 ETA={eta/60:.1f}分钟"
                )

        repo.commit()
        elapsed = time.time() - start
        log.info(
            f"第 {round_idx} 轮完成 · 耗时 {elapsed/60:.1f} 分钟 · "
            f"成功 {success} · 失败 {failed} · "
            f"新 sub_越界={new_outlier_sub} · 新 topic_越界={new_outlier_topic}"
        )

        if topic_changes:
            log.info("本轮 topic 变化:")
            for (old, new), cnt in topic_changes.most_common(10):
                log.info(f"  {old} → {new}: {cnt} 次")

        if new_outlier_sub == 0 and (only_sub or new_outlier_topic == 0):
            log.info(f"✅ 第 {round_idx} 轮：所有越界已消除，收敛完成")
            break

    session.close()

    # 完成后：DB 整体校验
    log.info("")
    log.info("=" * 70)
    log.info("最终 DB 越界检查")
    log.info("=" * 70)
    session2 = SessionLocal()
    from sqlalchemy import select
    from src.storage.db import Comment

    rows = list(session2.execute(select(Comment).where(Comment.analyzed_at.is_not(None)).limit(10000)).scalars())
    bad_sub: Counter = Counter()
    bad_topic: Counter = Counter()
    other_count = 0
    for c in rows:
        try:
            subs = json.loads(c.sub_topics) if c.sub_topics else []
        except Exception:
            subs = []
        for s in subs:
            if s not in all_l2:
                bad_sub[s] += 1
        if c.topic not in all_l1:
            bad_topic[c.topic or "(None)"] += 1
        if c.topic == "其他":
            other_count += 1
    session2.close()

    log.info(f"检查总数: {len(rows)} 条")
    log.info(f"sub_topics 越界: {sum(bad_sub.values())} 处（{len(bad_sub)} 种）")
    log.info(f"topic 越界: {sum(bad_topic.values())} 条")
    log.info(f"topic=其他: {other_count} 条（{other_count*100/len(rows):.1f}%）")
    if bad_sub:
        for s, c in bad_sub.most_common(10):
            log.info(f"  越界 sub: {s} ({c}次)")

    # 弹窗通知
    other_pct = other_count * 100 / len(rows) if rows else 0
    stats = {
        "total_analyzed": len(rows),
        "sub_outliers": sum(bad_sub.values()),
        "topic_outliers": sum(bad_topic.values()),
        "other_count": other_count,
        "other_pct": f"{other_pct:.1f}",
        "rounds_used": round_idx,
        "elapsed_minutes": f"{elapsed/60:.1f}",
    }
    notify_user(
        "VoC 重打标完成",
        f"耗时约 {elapsed/60:.0f} 分钟\n"
        f"sub_越界: {sum(bad_sub.values())} 处\n"
        f"topic=其他: {other_count} 条 ({other_pct:.1f}%)\n\n"
        f"请回到对话窗口查看详细分布报告",
        stats=stats,
    )


if __name__ == "__main__":
    main()