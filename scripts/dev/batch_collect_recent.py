"""批量补全 + 完整采集脚本（2026-08-05 00:05 一次性任务）

任务清单：
1. 补全 7 款游戏 8/4 的数据（Dota 2 已采过，跳过）
2. Dota 2 / PUBG / Apex 完整采集 8/1~8/4（与其他 7 款对齐）
3. 期间验证页机制自然生效（带时间窗 = 验证页启用）

业务约束：
- 仅采集不分析（用户明确 P0 暂不操作）
- STEAM_API_KEY 缺失：appreviews 端点不依赖 key，不阻塞
- max_count=500 单游戏上限
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.pipeline import run_pipeline  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.batch")


# === 第一批：补全 8/4 当天数据（Dota 2 已采过，跳过）===
GAMES_8_4_BACKFILL = [
    ("730", "Counter-Strike 2"),
    ("2358720", "黑神话：悟空"),
    ("1222140", "底特律：化身为人"),
    ("292030", "巫师 3：狂猎"),
    ("289070", "文明 6"),
    ("1903340", "光与影：33号远征队"),
    ("753640", "星际拓荒"),
]

# === 第二批：3 款游戏完整采集 8/1~8/4（Dota 2 / PUBG / Apex）===
GAMES_FULL_8_1_TO_8_4 = [
    ("570", "Dota 2"),       # 8/4 已采 17 条，8/1-8/3 需补
    ("578080", "PUBG"),      # 完全未采过
    ("1172470", "Apex Legends"),  # 完全未采过
]

WINDOW_8_4 = (
    datetime(2026, 8, 4, 0, 0, 0),
    datetime(2026, 8, 5, 0, 0, 0),
)
WINDOW_8_1_TO_8_4 = (
    datetime(2026, 8, 1, 0, 0, 0),
    datetime(2026, 8, 5, 0, 0, 0),
)


def main() -> None:
    summary: list[dict] = []

    log.info("=" * 70)
    log.info("第一批：补全 7 款游戏 8/4 数据")
    log.info("=" * 70)
    for appid, name in GAMES_8_4_BACKFILL:
        log.info(f"--- {name} (appid={appid}) 8/4 当天 ---")
        report = run_pipeline(
            platform="steam",
            target_id=appid,
            max_count=500,
            language="schinese",
            skip_analysis=True,
            posted_after=WINDOW_8_4[0],
            posted_before=WINDOW_8_4[1],
        )
        summary.append({
            "batch": "8/4 补全",
            "appid": appid,
            "name": name,
            "fetched": report.get("fetched", 0),
        })

    log.info("")
    log.info("=" * 70)
    log.info("第二批：Dota 2 / PUBG / Apex 完整采集 8/1~8/4")
    log.info("=" * 70)
    for appid, name in GAMES_FULL_8_1_TO_8_4:
        log.info(f"--- {name} (appid={appid}) 8/1~8/4 ---")
        report = run_pipeline(
            platform="steam",
            target_id=appid,
            max_count=500,
            language="schinese",
            skip_analysis=True,
            posted_after=WINDOW_8_1_TO_8_4[0],
            posted_before=WINDOW_8_1_TO_8_4[1],
        )
        summary.append({
            "batch": "8/1~8/4 完整",
            "appid": appid,
            "name": name,
            "fetched": report.get("fetched", 0),
        })

    log.info("")
    log.info("=" * 70)
    log.info("汇总")
    log.info("=" * 70)
    log.info(f"{'批次':<14} {'appid':<10} {'名称':<22} {'采集':>6}")
    total = 0
    for s in summary:
        log.info(
            f"{s['batch']:<14} {s['appid']:<10} {s['name']:<22} {s['fetched']:>6}"
        )
        total += s["fetched"]
    log.info(f"{'总计':<14} {'':<10} {'':<22} {total:>6}")


if __name__ == "__main__":
    main()