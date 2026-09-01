"""批量采集：6 款 Steam 游戏 × 8/1-8/3 中文评论

按用户要求：
- 时间范围：2026-08-01 00:00 ~ 2026-08-04 00:00
- 6 款游戏：黑神话悟空 / 巫师3 / 文明6 / 底特律化身为人 / 光与影 33号远征队 / 星际拓荒
- 数量：每款最多 500 条
- 已采集的：CS2 已有 200 条（8/1-8/3）
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.collectors.steam import SteamCollector
from src.storage.db import CommentRepository, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("batch_collect")

# 6 款游戏
GAMES = [
    {"appid": "2358720", "name": "黑神话：悟空"},  # Black Myth: Wukong
    {"appid": "292030", "name": "巫师 3：狂猎"},  # The Witcher 3: Wild Hunt
    {"appid": "289070", "name": "文明 6"},  # Sid Meier's Civilization VI（开发者未本地化，用公认中文名）
    {"appid": "1222140", "name": "底特律：化身为人"},  # Detroit: Become Human（官方名，非"变人"）
    {"appid": "1903340", "name": "光与影：33号远征队"},  # Clair Obscur: Expedition 33
    {"appid": "753640", "name": "星际拓荒"},  # Outer Wilds（开发者未本地化，用公认中文名）
]

POSTED_AFTER = datetime(2026, 8, 1, 0, 0, 0)
POSTED_BEFORE = datetime(2026, 8, 4, 0, 0, 0)
MAX_COUNT = 500

collector = SteamCollector()
_, SessionLocal = init_db()


def collect_one(appid: str, display_name: str) -> int:
    """采集单个游戏的 8/1-8/3 中文评论。返回实际入库条数。"""
    log.info(f"=== 采集 {appid} ({display_name}) ===")
    # 1. 先拉游戏元数据
    info = collector.fetch_app_info(appid)
    if info:
        # 验证 Steam 官方名（部分游戏 Steam 仍返回英文）
        steam_name = info.get("name", "")
        log.info(f"  Steam 官方名: {steam_name!r}")
        # 用我们确认的中文名作为权威（用户的"用官方中文名"原则）
        target_meta = {"name": display_name, "type": info.get("type")}
    else:
        log.warning(f"  fetch_app_info 失败，使用用户提供名")
        target_meta = {"name": display_name, "type": "game"}

    # 2. 拉评论（posted_after 用 Steam API day_range；posted_before 应用层）
    raws = collector.collect(
        target_id=appid,
        max_count=MAX_COUNT,
        language="schinese",  # 顶层原则
        posted_after=POSTED_AFTER,
        posted_before=POSTED_BEFORE,
    )
    log.info(f"  拉取到 {len(raws)} 条 schinese 评论")

    if not raws:
        log.warning(f"  没有拉取到任何评论，跳过")
        return 0

    # 3. 持久化
    with SessionLocal() as s:
        repo = CommentRepository(s)
        inserted = repo.bulk_upsert(raws, target_meta=target_meta)
        s.commit()
    log.info(f"  入库 {inserted} 条")
    return inserted


def main():
    log.info(f"===== 开始批量采集：{len(GAMES)} 款游戏 × {MAX_COUNT} 条上限 =====")
    log.info(f"时间范围: {POSTED_AFTER.date()} ~ {POSTED_BEFORE.date()}")
    total = 0
    summary = []
    for game in GAMES:
        n = collect_one(game["appid"], game["name"])
        summary.append((game["appid"], game["name"], n))
        total += n

    log.info(f"\n===== 汇总 =====")
    for appid, name, n in summary:
        log.info(f"  {appid} {name}: {n} 条")
    log.info(f"\n总计入库: {total} 条")


if __name__ == "__main__":
    main()