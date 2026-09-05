"""P6 自动化采集编排入口

每日增量采集的主入口。被 .github/workflows/daily-collect.yml 调用，
也可本地手动运行做调试或离线补采。

核心职责：
1. 加载监控目标清单（config/monitoring/targets.yaml）
2. 计算每个目标的 posted_after 滑窗（max(posted_at) - 1 天）
3. 对每个 enabled=true 的目标调用 run_pipeline（增量）
4. 汇总今日新增 / 累计 / 失败清单
5. （可选）把 DB 上传到 GitHub Release 作为长期累积载体

设计要点：
- 单 target 失败不阻塞其他（try/except 包裹）
- 增量语义完全依赖 src/pipeline.py 现有能力（bulk_upsert 去重 +
  analyzed_at IS NOT NULL 跳过 + find_missing_embedding_ids 增量向量化），
  本脚本不引入新的存储逻辑
- 上传 GH Release 失败仅记 warning，不中断主流程
- 兼容 voc-daily-bootstrap 基线 Release（首次跑或新克隆仓库）

使用：
    # GitHub Actions 调用（自动下载前一日 release + 上传今日 release）
    python scripts/ops/daily_incremental_collect.py

    # 本地调试（不下载/上传 release）
    python scripts/ops/daily_incremental_collect.py --no-download --no-upload

    # 本地手动同步（强制从头跑，禁用滑窗）
    python scripts/ops/daily_incremental_collect.py --no-download --no-upload --full-replay
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 让脚本从项目根直接运行
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src.pipeline import run_pipeline  # noqa: E402
from src.storage.db import init_db, _utcnow  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.daily_incremental")


# ---------- 配置加载 ----------

DEFAULT_TARGETS = ROOT / "config" / "monitoring" / "targets.yaml"
DEFAULT_DB_PATH = ROOT / "data" / "voc.db"
DEFAULT_RELEASE_TAG = "voc-daily"
DB_ASSET_NAME = "voc.db"
# GitHub 网页端上传同名文件会被自动加后缀（如 voc.db-1）；脚本兼容这种情况：
# 先用 --pattern 精确匹配，失败则按前缀 voc.db.* 接受带后缀的文件。
DB_ASSET_PREFIX = "voc.db"

# 北京时区固定偏移（UTC+8）。用 timedelta 而非 zoneinfo 避免 DST 影响（中国不实行夏令时）。
BJT_OFFSET = timedelta(hours=8)


def load_targets(config_path: Path) -> list[dict]:
    """加载 targets.yaml，返回启用的目标列表（保留原顺序）

    注：2026-09-01 起 DB（collect_tasks 表）为首选目标来源（Web 看板可增删改），
    本函数降级为「空表回退」路径，见 load_targets_from_db / load_targets_any。
    """
    if not config_path.exists():
        raise FileNotFoundError(f"监控配置不存在：{config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    targets = [t for t in cfg.get("targets", []) if t.get("enabled", True)]
    log.info(f"加载监控目标 {len(targets)} 个（enabled，yaml）")
    for t in targets:
        log.info(f"  · {t['platform']}:{t['id']} {t.get('name', '')} count={t.get('count')}")
    return targets


def load_targets_from_db(db_path: Path | None = None) -> list[dict] | None:
    """从 collect_tasks 表加载启用的 Steam 目标（Web 看板管理入口）

    Returns:
        目标 dict 列表（格式对齐 targets.yaml 条目：platform/id/name/language/count）；
        表为空（或无 enabled 任务）返回 None —— 由调用方回退 yaml 并种子化。
    """
    from src.storage.db import CollectTaskRepository, init_db

    db_url = f"sqlite:///{db_path}" if db_path else None
    _, SessionLocal = init_db(db_url)
    with SessionLocal() as s:
        tasks = CollectTaskRepository(s).list_all(enabled_only=True, platform="steam")
        if not tasks:
            return None
        targets = [
            {
                "platform": "steam",
                "id": t.target_id,
                "name": t.name or t.target_id,
                "language": t.language or "schinese",
                "count": t.count,
                "task_row_id": t.id,  # 透传内部 id，采集侧可回写 last_collected_at
            }
            for t in tasks
        ]
    log.info(f"加载监控目标 {len(targets)} 个（enabled，DB collect_tasks）")
    for t in targets:
        log.info(f"  · steam:{t['id']} {t['name']} count={t['count']}")
    return targets


def load_targets_any(config_path: Path, db_path: Path | None = None) -> list[dict]:
    """DB 优先加载目标；空表时先种子化（yaml → collect_tasks）再重试，仍空则回退 yaml

    种子化幂等：已存在的 platform+target_id 跳过；excluded_targets 不迁移。
    """
    from src.storage.db import seed_collect_tasks_from_yaml, init_db

    db_targets = load_targets_from_db(db_path)
    if db_targets:
        return db_targets

    # 空表 → 尝试从 yaml 种子化后重试一次
    if config_path.exists():
        db_url = f"sqlite:///{db_path}" if db_path else None
        _, SessionLocal = init_db(db_url)
        with SessionLocal() as s:
            seeded = seed_collect_tasks_from_yaml(s, config_path)
        if seeded:
            log.info(f"已从 targets.yaml 种子化 {seeded} 条目标到 collect_tasks")
            db_targets = load_targets_from_db(db_path)
            if db_targets:
                return db_targets

    log.info("collect_tasks 为空且无可种子化条目，回退 targets.yaml")
    return load_targets(config_path)


# ---------- 时间窗计算 ----------

def calc_posted_after(target_id_with_platform: str, *, lookback_days: int = 1) -> datetime | None:
    """计算目标的 posted_after 滑窗起点 = max(posted_at) - lookback_days

    Args:
        target_id_with_platform: 形如 "steam:2358720"
        lookback_days: 向前回看天数（默认 1 天，覆盖边界漏采）

    Returns:
        naive UTC datetime；若目标尚无评论则返回 None（全量起步）
    """
    from sqlalchemy import select, func
    from src.storage.db import Comment, init_db

    _, SessionLocal = init_db()
    with SessionLocal() as s:
        stmt = select(func.max(Comment.posted_at)).where(
            Comment.target_id == target_id_with_platform
        )
        max_ts = s.execute(stmt).scalar()
    if max_ts is None:
        return None
    # max_ts 是 naive UTC，向前 lookback_days；转回 naive UTC 便于 run_pipeline 直接用
    return (max_ts - timedelta(days=lookback_days))


def smart_window(
    target_id_with_platform: str,
    now_utc: datetime,
    lookback_days: int = 2,
) -> tuple[datetime, datetime]:
    """智能时间窗口（以北京日历日为准）

    每天采「北京 lookback_days 天前 0:00 → 北京今天 0:00」的 lookback_days 个日历日
    （auto 模式翻页到窗外，upsert 去重；已分析评论自动跳过，重复采集无 LLM 成本）。
    当天（北京时间）严格不采（posted_before 卡死边界）。

    2026-09-03 扩窗：默认 lookback_days 2 → 计划任务传 7。原因：Steam
    `filter=recent` 游标流是非确定性采样（同窗口每次爬取子集不同，单次漏 5-20%），
    多日重叠回看 + upsert 幂等使覆盖率随多遍采样收敛。成本仅分页加深（7 页/游戏）。

    Args:
        target_id_with_platform: 形如 "steam:2358720"
        now_utc: 当前 UTC 时间（naive 或 aware 都接受；统一转 naive UTC）
        lookback_days: 回看天数（2 = 昨天+前天；7 = 近 7 个日历日）

    Returns:
        (posted_after, posted_before) 都是 naive UTC
        posted_before: 北京当天 0:00 UTC 表示（= UTC T-1 16:00），不采当天
        posted_after: max(target_posted_after, 北京 lookback_days 天前 0:00 UTC 表示)
            target_posted_after = max(posted_at) - 1 day（复用 calc_posted_after 拿到的）
            floor = 北京 lookback_days 天前 0:00 UTC 表示（补救窗口下限）
    """
    # aware → naive（_utcnow 返回 naive，外部传入 aware 也兼容）
    if now_utc.tzinfo is not None:
        now_utc = now_utc.replace(tzinfo=None)

    # 北京日历日：now_utc + 8h 取日历日部分
    now_bjt = now_utc + BJT_OFFSET
    today_bjt_midnight = now_bjt.replace(hour=0, minute=0, second=0, microsecond=0)

    # posted_before: 北京当天 0:00 → 转 UTC 表示
    posted_before = today_bjt_midnight - BJT_OFFSET
    # posted_after floor: 北京 lookback_days 天前 0:00 → 转 UTC 表示
    posted_after_floor = posted_before - timedelta(days=lookback_days)

    # 复用 calc_posted_after（默认 lookback_days=1，即 max_ts - 1 day）
    target_posted_after = calc_posted_after(target_id_with_platform)
    if target_posted_after is None:
        # 空 DB：起步采 lookback_days 天（floor 起作用）
        return (posted_after_floor, posted_before)

    return (max(target_posted_after, posted_after_floor), posted_before)


# ---------- GH Release 操作 ----------

def gh_release_exists(tag: str) -> bool:
    """检查 GH Release 是否存在（gh CLI）"""
    r = subprocess.run(
        ["gh", "release", "view", tag],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def gh_release_download(tag: str, db_path: Path) -> bool:
    """下载指定 release 的 voc.db asset 到 db_path（覆盖）

    Returns:
        True=下载成功；False=release 不存在或下载失败
    """
    if not gh_release_exists(tag):
        log.warning(f"Release {tag} 不存在，将以空库起步")
        return False

    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 先列出 release 下的所有 asset 名称，挑出以 voc.db 开头的（兼容网页端上传时自动加 -1/-2 后缀）
    list_r = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets", "--jq", ".assets[].name"],
        capture_output=True, text=True,
    )
    if list_r.returncode != 0:
        log.warning(f"读取 release {tag} asset 列表失败：{list_r.stderr.strip()}")
        return False
    asset_names = [line.strip() for line in list_r.stdout.splitlines() if line.strip()]
    asset_name = next(
        (n for n in asset_names if n == DB_ASSET_NAME),
        next((n for n in asset_names if n.startswith(DB_ASSET_PREFIX)), None),
    )
    if not asset_name:
        log.warning(f"release {tag} 下未找到 {DB_ASSET_PREFIX}* asset（实际：{asset_names}）")
        return False

    # 先下到临时文件，下载成功再改名（避免半成品覆盖已有 DB）
    tmp = db_path.with_suffix(db_path.suffix + ".download")
    if tmp.exists():
        tmp.unlink()
    r = subprocess.run(
        ["gh", "release", "download", tag, "--pattern", asset_name, "-O", str(tmp)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        log.warning(f"下载 release {tag} 的 {asset_name} 失败：{r.stderr.strip()}")
        return False
    # 原子替换
    tmp.replace(db_path)
    log.info(f"已下载 release {tag} 的 {asset_name} → {db_path}")
    return True


def gh_release_upload(tag: str, db_path: Path, *, force: bool = True) -> bool:
    """上传 DB 作为 GH Release asset（创建 release 若不存在）

    Args:
        tag: release tag（如 voc-daily-2026-08-19）
        db_path: 要上传的 DB 文件
        force: 若 asset 已存在是否覆盖（默认 True —— DB 增长，每天覆盖）

    Returns:
        True=上传成功；False=失败
    """
    if not db_path.exists():
        log.error(f"待上传 DB 不存在：{db_path}")
        return False

    # 确保 release 存在（gh release create 已存在返回非 0 但不致命）
    subprocess.run(
        ["gh", "release", "create", tag, "--generate-notes"],
        capture_output=True, text=True,
    )
    flags = ["--clobber"] if force else []
    # 不要传 --name：gh CLI 新版不再支持，basename 已与 DB_ASSET_NAME 一致（voc.db）
    # 旧版本会传 ["--name", DB_ASSET_NAME]，触发 "unknown flag: --name" 导致 upload 失败
    # 但 daily 仍标 success（脚本只记 warning）→ release assets=[] 累积载体失效
    r = subprocess.run(
        ["gh", "release", "upload", tag, str(db_path)] + flags,
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        log.info(f"已上传 {db_path.name} → release {tag}")
        return True
    log.warning(f"上传 release {tag} 失败：{r.stderr.strip()}")
    return False


# ---------- 主流程 ----------

def today_tag(prefix: str = "voc-daily") -> str:
    """生成今日 release tag：voc-daily-YYYY-MM-DD"""
    return f"{prefix}-{_utcnow().strftime('%Y-%m-%d')}"


def run_one_target(
    target: dict,
    *,
    now_utc: datetime,
    full_replay: bool = False,
    lookback_days: int = 2,
) -> dict:
    """对单个目标运行一次 run_pipeline（增量）

    Args:
        target: targets.yaml 单条目标配置
        now_utc: 整批共享的「当前时间」（naive UTC），保证所有 target 的窗口基准一致
        full_replay: True 时禁用滑窗（posted_after/Before=None），全量起步

    Returns:
        {"target": ..., "ok": bool, "fetched": int, "analyzed": int, "embedded": int, "error": str|None}
    """
    platform = target["platform"]
    tid = str(target["id"])
    name = target.get("name", tid)
    count = target.get("count", 50)
    label = f"{platform}:{tid} ({name})"

    # 计算 posted_after / posted_before 滑窗（以北京日历日为准）
    posted_after = None
    posted_before = None
    if not full_replay:
        target_id_with_platform = f"{platform}:{tid}"
        posted_after, posted_before = smart_window(
            target_id_with_platform, now_utc, lookback_days=lookback_days
        )

    log.info(f"── 增量采集 {label} ──")
    log.info(
        f"   posted_after={posted_after} posted_before={posted_before} "
        f"count={count} full_replay={full_replay}"
    )

    try:
        report = run_pipeline(
            platform=platform,
            target_id=tid,
            max_count=count,
            language=target.get("language", "schinese"),
            posted_after=posted_after,
            posted_before=posted_before,
            skip_analysis=False,
        )
        return {
            "target": label,
            "ok": True,
            "fetched": report.get("fetched", 0),
            "analyzed": report.get("analyzed", 0),
            "embedded": report.get("embedded", 0),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        log.exception(f"  ✗ {label} 失败")
        return {
            "target": label,
            "ok": False,
            "fetched": 0,
            "analyzed": 0,
            "embedded": 0,
            "error": str(e),
        }


def emit_step_summary(results: list[dict]) -> None:
    """把今日摘要写到 $GITHUB_STEP_SUMMARY（GitHub Actions 用），本地运行则仅日志"""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        for r in results:
            log.info(f"  · {r['target']}: ok={r['ok']} fetched={r['fetched']} "
                     f"analyzed={r['analyzed']} embedded={r['embedded']} "
                     f"error={r['error']}")
        return

    ok = sum(1 for r in results if r["ok"])
    fail = len(results) - ok
    fetched_total = sum(r["fetched"] for r in results)
    analyzed_total = sum(r["analyzed"] for r in results)

    lines = [
        f"# 每日 VoC 采集摘要 · {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "",
        f"- ✅ 成功：{ok} / ❌ 失败：{fail} / 📊 今日新增 fetched={fetched_total}, analyzed={analyzed_total}",
        "",
        "| 目标 | 状态 | 采集 | 分析 | 向量 | 错误 |",
        "|------|------|------|------|------|------|",
    ]
    for r in results:
        lines.append(
            f"| {r['target']} | {'✅' if r['ok'] else '❌'} | "
            f"{r['fetched']} | {r['analyzed']} | {r['embedded']} | "
            f"{r['error'] or '—'} |"
        )
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description="P6 每日增量采集编排入口")
    parser.add_argument("--targets-config", default=str(DEFAULT_TARGETS),
                        help="监控目标 YAML 路径")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH),
                        help="本地 SQLite DB 路径")
    parser.add_argument("--release-tag-prefix", default=DEFAULT_RELEASE_TAG,
                        help="GH Release tag 前缀（默认 voc-daily）")
    parser.add_argument("--bootstrap-tag", default=f"{DEFAULT_RELEASE_TAG}-bootstrap",
                        help="基线 Release tag（首次跑或新克隆时使用）")
    parser.add_argument("--no-download", action="store_true",
                        help="跳过下载历史 release（本地调试用）")
    parser.add_argument("--no-upload", action="store_true",
                        help="跳过上传今日 release（本地调试用）")
    parser.add_argument("--full-replay", action="store_true",
                        help="禁用 posted_after 滑窗，对每个目标做全量采集（与已有评论去重）")
    parser.add_argument("--lookback-days", type=int, default=2,
                        help="回看天数（北京日历日）。2=昨天+前天；本地直采计划任务传 7 "
                             "（多日重叠采样对冲 Steam recent 流非确定性，2026-09-03）")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    targets_cfg = Path(args.targets_config)

    # 1. 下载历史 DB（如启用）
    if not args.no_download:
        # 优先拉昨日 release，失败则回退 bootstrap，失败则以空库起步
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        yest_tag = f"{args.release_tag_prefix}-{yesterday}"
        if not gh_release_download(yest_tag, db_path):
            if not gh_release_download(args.bootstrap_tag, db_path):
                log.warning("无历史 DB 可用，以空库起步")

    # 2. 初始化 DB（首次跑 / 下载失败时新建表结构）
    init_db(f"sqlite:///{db_path}")

    # 3. 加载并执行目标清单（DB collect_tasks 优先；空表自动从 yaml 种子化后重试；
    #    仍空则回退 yaml —— 2026-09-01 Web 看板 collect_tasks 迁移，见 WEB_DASHBOARD.md §3.4）
    targets = load_targets_any(targets_cfg, db_path)
    if not targets:
        log.warning("无 enabled 目标（DB 与 targets.yaml 均为空），退出")
        return

    results = []
    # 整批共享同一 now_utc，保证所有 target 的窗口基准一致
    now_utc = _utcnow()
    for t in targets:
        results.append(run_one_target(t, now_utc=now_utc, full_replay=args.full_replay,
                                      lookback_days=args.lookback_days))

    # 4. 写步骤摘要
    emit_step_summary(results)

    # 5. 上传今日 release（如启用）
    if not args.no_upload:
        tag = today_tag(args.release_tag_prefix)
        if not gh_release_upload(tag, db_path):
            log.warning("今日 release 上传失败；DB 仍在本地，下次跑会从本地 DB 起步")

    # 6. 退出码：任一失败 → 非零
    if any(not r["ok"] for r in results):
        log.warning("部分目标失败，非零退出")
        sys.exit(1)
    log.info("===== 全部目标完成 =====")


if __name__ == "__main__":
    main()