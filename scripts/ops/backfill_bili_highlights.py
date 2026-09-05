"""存量 B 站视频「快照 + 弹幕高光总结」回填（2026-09-04 · B站视频看板）

背景：bilibili_queue 快照列（封面/UP主/播放量/三连/时长/标签）与 highlights_json
是 2026-09-04 才加的，存量 fetched 视频需要一次性回填；此后新采集由 pipeline
自动完成（_snapshot_bili_queue / _generate_danmaku_highlights），无需再跑本脚本。

用法（项目根运行）：
  python scripts/ops/backfill_bili_highlights.py              # 全部 fetched 视频
  python scripts/ops/backfill_bili_highlights.py BV1xxx ...   # 指定 BV

依赖：.env 中的 LLM Key（默认 DEEPSEEK_API_KEY）；B 站 view 接口可访问。
成本：每视频 1 次 view 调用 + 3 次 LLM 总结。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from src.collectors.bilibili import BilibiliCollector  # noqa: E402
from src.pipeline import _generate_danmaku_highlights, _snapshot_bili_queue  # noqa: E402
from src.storage.db import BilibiliQueue, Comment, init_db  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="B站视频快照+高光总结回填")
    parser.add_argument("bv_ids", nargs="*", help="指定 BV 号；缺省=全部有数据的视频")
    parser.add_argument("--provider", default="deepseek", help="LLM provider（默认 deepseek）")
    args = parser.parse_args()

    _, SessionLocal = init_db()
    collector = BilibiliCollector()

    # 待回填集合：queue fetched 行的 bv + 库内已有评论的 target_id 反推 aid（队列表可能为空）
    aids: dict[int, str] = {}  # aid → bv_id（可空）
    with SessionLocal() as s:
        if args.bv_ids:
            for row in s.execute(
                select(BilibiliQueue).where(BilibiliQueue.bv_id.in_(args.bv_ids))
            ).scalars():
                if row.status == "fetched" and row.aid:
                    aids[row.aid] = row.bv_id
        else:
            for row in s.execute(
                select(BilibiliQueue).where(BilibiliQueue.status == "fetched")
            ).scalars():
                if row.aid:
                    aids.setdefault(row.aid, row.bv_id)
            # 队列表可能为空（存量数据未入队）→ 从评论 target_id 反推
            for (tid,) in s.execute(
                select(Comment.target_id).where(Comment.platform == "bilibili").distinct()
            ):
                if tid and tid.startswith("bilibili:video:"):
                    aids.setdefault(int(tid.rsplit(":", 1)[1]), "")

    if not aids:
        print("没有可回填的视频")
        return

    ok, fail = 0, 0
    for aid, bv in aids.items():
        print(f"── aid={aid} bv={bv or '?'}")
        try:
            info = collector.fetch_video_info(str(aid))
            if not info:
                raise RuntimeError("view 接口未返回数据")
            _snapshot_bili_queue(info.get("bvid") or bv, info)
            _generate_danmaku_highlights(info["aid"], provider=args.provider)
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  [FAIL] {type(e).__name__}: {e}")

    print(f"完成：成功 {ok} / 失败 {fail}")


if __name__ == "__main__":
    main()
