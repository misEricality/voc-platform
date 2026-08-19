"""补齐评论数据到 2026-08-16（一次性任务 · 只采集不分析）

背景（实测 data/voc.db，2026-08-18）：
- Steam 采集范围 = 6 款单机游戏（不含网游）：最新 posted_at = 2026-08-04，缺 08-04 → 08-16 约 12 天
- B站 1 条视频评论最新 posted_at = 2026-08-12，缺 08-12 → 08-16 约 4 天（当前暂不采集）

策略：
- 仅采集不分析（先采后分析，两阶段；分析走 DeepSeek 另跑，见文末"后续步骤"）
- Steam：appreviews 端点不依赖 API Key；时间窗走应用层 posted_after / posted_before
- B站：采集器不支持时间窗过滤（fetch_comments 用 **kwargs 忽略 posted_*），
  只能全量重采 + 按 source_id 去重；热门视频需 SESSDATA（.env 的 BILIBILI_SESSDATA），
  否则匿名降级只给 3 条；请求间隔 ≥1.2s 已内置防风控
- 自动量模式（默认）：Steam 不设 max_count（None），靠时间窗 + 采集器自然耗尽
  各游戏的窗口量，量随游戏/阶段/时间区间自适应（详见 src/collectors/steam.py）
- 也可 --max-count N 显式设硬上限（N 作为安全阀，限制单游戏拉取量）

用法：
    python scripts/dev/backfill_0816.py                 # 仅 Steam（默认，自动量）
    python scripts/dev/backfill_0816.py --platform all  # Steam + B站
    python scripts/dev/backfill_0816.py --platform bilibili
    python scripts/dev/backfill_0816.py --max-count 1000  # 显式单游戏采集上限（安全阀）
    python scripts/dev/backfill_0816.py --detect        # 只探测各游戏窗口真实量，不发请求落库
    python scripts/dev/backfill_0816.py --dry-run       # 只打印清单，不发请求

后续步骤（本脚本不执行，跑完确认后再做）：
    1) 分析新评论：python -m src.pipeline --platform steam --target <appid> --count 500 --language schinese
       （逐游戏，不带 --skip-analysis；DeepSeek ≈ ¥3-6/千条）
    2) 向量回填：python scripts/ops/backfill_embeddings.py（1021 → 全量）
    3) P0 全量重打：GDT v3.1.1 词典扩充 + 旧标签清洗
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.pipeline import run_pipeline  # noqa: E402
from src.collectors.steam import SteamCollector  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.backfill0816")

# 6 款 Steam 单机游戏（仅单机，不含网游；与 data/voc.db 现有 target_id 对齐）
STEAM_GAMES = [
    ("2358720", "黑神话：悟空"),
    ("1222140", "底特律：化身为人"),
    ("292030", "巫师 3：狂猎"),
    ("289070", "文明 6"),
    ("1903340", "光与影：33号远征队"),
    ("753640", "星际拓荒"),
]

# B站视频（1 条；bvid 传采集器，落库 target_id 为 bilibili:video:{aid}）
BILI_VIDEOS = [
    ("BV1UpwaeNESx", "B站评测视频（BV1UpwaeNESx）"),
]

# 补采时间窗（Steam 08-04 起；上界 BEFORE 开区间 → 含 08-16 全天）
STEAM_AFTER = datetime(2026, 8, 4, 0, 0, 0)
BEFORE = datetime(2026, 8, 17, 0, 0, 0)


def _detect_steam() -> list[dict]:
    """探测各游戏时间窗内真实量（不发落库请求，仅计数）

    用采集器 + 时间窗自然耗尽逻辑，数出每款游戏窗口内真实评论量，
    不落库、不向量化。用于决定是否需要采集 / 采多少。
    """
    summary: list[dict] = []
    collector = SteamCollector()
    for appid, name in STEAM_GAMES:
        n = 0
        try:
            for _ in collector.fetch_comments(
                appid,
                max_count=None,  # 自动量：靠时间窗自然耗尽
                language="schinese",
                posted_after=STEAM_AFTER,
                posted_before=BEFORE,
            ):
                n += 1
        except Exception as e:  # noqa: BLE001 - 单游戏失败不阻塞整体
            log.warning(f"  {name} (appid={appid}) 探测失败: {e}")
            n = -1
        summary.append({"platform": "steam", "target": appid, "name": name, "window_count": n})
        log.info(f"  {name:<18} appid={appid:<8} 窗口量={n}")
    return summary


def _collect_steam(dry_run: bool, max_count: int | None) -> list[dict]:
    summary: list[dict] = []
    for appid, name in STEAM_GAMES:
        log.info(f"--- Steam {name} (appid={appid}) 08-04 → 08-16 ---")
        if dry_run:
            summary.append({"platform": "steam", "target": appid, "name": name, "fetched": None})
            continue
        report = run_pipeline(
            platform="steam",
            target_id=appid,
            max_count=max_count,  # None = 自动量（默认）
            language="schinese",
            skip_analysis=True,
            posted_after=STEAM_AFTER,
            posted_before=BEFORE,
        )
        summary.append({
            "platform": "steam",
            "target": appid,
            "name": name,
            "fetched": report.get("fetched", 0),
        })
    return summary


def _collect_bilibili(dry_run: bool) -> list[dict]:
    summary: list[dict] = []
    for bvid, name in BILI_VIDEOS:
        log.info(f"--- B站 {name} (bvid={bvid}) 全量重采+去重 ---")
        if dry_run:
            summary.append({"platform": "bilibili", "target": bvid, "name": name, "fetched": None})
            continue
        # 注意：B站无时间窗，传 posted_* 会被忽略；全量重采后按 source_id 去重。
        report = run_pipeline(
            platform="bilibili",
            target_id=bvid,
            max_count=1000,
            skip_analysis=True,
        )
        summary.append({
            "platform": "bilibili",
            "target": bvid,
            "name": name,
            "fetched": report.get("fetched", 0),
        })
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="补齐评论数据到 2026-08-16（只采集）")
    parser.add_argument(
        "--platform",
        choices=["steam", "bilibili", "all"],
        default="steam",
        help="采集平台（默认 steam；B站无时间窗、走全量重采+去重）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印采集清单，不发任何请求",
    )
    parser.add_argument(
        "--max-count",
        type=int,
        default=None,
        help="单游戏采集上限（默认 None=自动量：靠时间窗自然耗尽，各游戏量自适应）",
    )
    parser.add_argument(
        "--detect",
        action="store_true",
        help="只探测各游戏时间窗内真实量（计数，不落库/不向量化）",
    )
    args = parser.parse_args()

    # 探测模式：只报量，不发请求落库
    if args.detect:
        if args.platform in ("steam", "all"):
            det = _detect_steam()
            log.info("")
            log.info("=" * 60)
            log.info("窗口真实量探测（只计数，未采集）")
            log.info("=" * 60)
            for d in det:
                shown = str(d["window_count"]) if d["window_count"] >= 0 else "失败"
                log.info(f"  {d['name']:<18} appid={d['target']:<8} 窗口量={shown}")
        else:
            log.warning("--detect 目前仅支持 steam/all（B站无时间窗，量=全量重采后去重）")
        return

    summary: list[dict] = []
    if args.platform in ("steam", "all"):
        summary += _collect_steam(args.dry_run, args.max_count)
    if args.platform in ("bilibili", "all"):
        summary += _collect_bilibili(args.dry_run)

    log.info("")
    log.info("=" * 70)
    log.info(f"汇总（dry_run={args.dry_run}）")
    log.info("=" * 70)
    log.info(f"{'平台':<9} {'target':<12} {'名称':<24} {'采集':>6}")
    total = 0
    for s in summary:
        fetched = s["fetched"] if s["fetched"] is not None else 0
        total += fetched
        shown = str(s["fetched"]) if s["fetched"] is not None else "-"
        log.info(f"{s['platform']:<9} {s['target']:<12} {s['name']:<24} {shown:>6}")
    log.info(f"{'总计':<9} {'':<12} {'':<24} {total:>6}")
    log.info("")
    log.info("下一步（跑完确认数据量后再做，见脚本顶部 docstring）：")
    log.info("  1) 分析新评论（逐游戏不带 --skip-analysis）")
    log.info("  2) python scripts/ops/backfill_embeddings.py  # 向量回填")
    log.info("  3) P0 全量重打 GDT v3.1.1 + 旧标签清洗")


if __name__ == "__main__":
    main()
