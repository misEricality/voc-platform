"""智能 sync GH Release → 本地 voc.db（Windows Task Scheduler 友好）

设计：
- 检查今天 release tag（voc-daily-{today UTC}）是否已上传
- 没上传 → 跳过（不报错，10:00 早跑常见）
- 上传了 + 比本地新 → sync（带 rename 容错，Streamlit 锁文件也能处理）
- 上传了 + 跟本地一样新 → noop
- 任何意外 → 退非 0，Task Scheduler 会记日志

幂等：重复跑没事，浪费 0 资源

使用：
    # 默认今天 UTC
    python scripts/ops/smart_sync_release.py

    # 指定日期（debug 用）
    python scripts/ops/smart_sync_release.py --date 2026-08-28

退出码：
- 0: 成功 / noop
- 1: 同步失败（文件锁、网络等）
- 2: 必需依赖（git/curl）缺失
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

REPO = "misEricality/voc-platform"
API = "https://api.github.com"
DB_PATH = ROOT / "data" / "voc.db"
DOWNLOAD_PATH = ROOT / "data" / "voc.db.download"
SWAP_PATH = ROOT / "data" / "voc.db.swap_in"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("voc.smart_sync")


def today_tag(date: datetime | None = None) -> str:
    """voc-daily-YYYY-MM-DD（UTC，对齐 GH 远端 tag 命名）"""
    d = date or datetime.now(timezone.utc)
    return f"voc-daily-{d.strftime('%Y-%m-%d')}"


def get_release(tag: str) -> dict | None:
    """GET /repos/.../releases/tags/{tag}; 不存在返回 None"""
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(
                f"{API}/repos/{REPO}/releases/tags/{tag}",
                headers={"Accept": "application/vnd.github+json", "User-Agent": "voc-smart-sync"},
            ),
            timeout=15,
        )
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def get_vocdb_asset(release: dict) -> dict | None:
    """找 voc.db* asset（兼容 voc.db-1/-2 后缀）"""
    for a in release.get("assets", []):
        if a.get("name") == "voc.db":
            return a
    for a in release.get("assets", []):
        if a.get("name", "").startswith("voc.db"):
            return a
    return None


def download_vocdb(asset: dict, dst: Path) -> int:
    """下载 asset 到 dst；返回 bytes 数"""
    url = asset["browser_download_url"]
    log.info(f"downloading {url}")
    r = urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "voc-smart-sync"}),
        timeout=120,
    )
    data = r.read()
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return len(data)


def safe_replace_db(new_path: Path, current_db: Path) -> None:
    """把 new_path 内容替换 current_db，避免 Streamlit 锁文件时 os.replace 失败

    策略：
    1. rename new_path → swap_in（原地）
    2. rename current_db → backup（Windows rename 不需要 no-handle）
       *但如果 current_db 被独占锁（Streamlit 运行中），这一步会失败
        → 抛 PermissionError；调用方应 catch + 提示用户关仪表盘
    3. rename swap_in → current_db
    4. delete backup
    """
    if SWAP_PATH.exists():
        SWAP_PATH.unlink()
    log.info(f"step 1/4: rename {new_path.name} -> {SWAP_PATH.name}")
    os.rename(new_path, SWAP_PATH)

    backup = current_db.with_suffix(current_db.suffix + ".oldsync")
    if backup.exists():
        backup.unlink()
    log.info(f"step 2/4: rename {current_db.name} -> {backup.name}")
    os.rename(current_db, backup)

    log.info(f"step 3/4: rename {SWAP_PATH.name} -> {current_db.name}")
    os.rename(SWAP_PATH, current_db)

    log.info(f"step 4/4: cleanup {backup.name}")
    backup.unlink()


def main() -> int:
    p = argparse.ArgumentParser(description="智能 sync GH Release → 本地 voc.db（cron 友好）")
    p.add_argument("--date", help="指定 UTC 日期（默认 today UTC）")
    p.add_argument("--repo", default=REPO)
    p.add_argument("--db", default=str(DB_PATH))
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.parent.exists():
        log.error(f"data dir not found: {db_path.parent}")
        return 1

    # 1. 解析目标 tag
    if args.date:
        try:
            d = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            log.error(f"invalid date: {args.date}")
            return 1
        tag = today_tag(d)
    else:
        tag = today_tag()
    log.info(f"target tag: {tag}")

    # 2. 查 release
    release = get_release(tag)
    if release is None:
        log.info(f"release {tag} not found on remote — workflow may not have completed yet; skip")
        return 0

    asset = get_vocdb_asset(release)
    if asset is None:
        log.error(f"release {tag} exists but has no voc.db asset — possible silent failure")
        return 1

    remote_size = asset.get("size", 0)
    remote_updated = asset.get("updated_at", "")
    log.info(f"remote asset: {asset.get('name')} {remote_size:,} bytes updated_at={remote_updated}")

    # 3. 比对本地 DB
    if db_path.exists():
        local_size = db_path.stat().st_size
        local_mtime = db_path.stat().st_mtime
        # 如果远端 mtime <= 本地 mtime（误差 5 分钟内认为 noop）
        from datetime import datetime as _dt
        try:
            remote_dt = _dt.fromisoformat(remote_updated.replace("Z", "+00:00"))
            remote_ts = remote_dt.timestamp()
            # 比较：远端 < 本地（5 分钟容差）= 已经更新过
            if remote_ts < local_mtime - 300:
                log.info(f"local DB mtime {local_mtime:.0f} >= remote mtime {remote_ts:.0f} + 5min — already up-to-date; skip")
                return 0
        except Exception as e:
            log.warning(f"could not compare mtime: {e}; proceeding with download")

    # 4. 下载到 .download
    if DOWNLOAD_PATH.exists():
        DOWNLOAD_PATH.unlink()
    try:
        n = download_vocdb(asset, DOWNLOAD_PATH)
    except Exception as e:
        log.error(f"download failed: {e}")
        return 1
    log.info(f"downloaded {n:,} bytes to {DOWNLOAD_PATH.name}")

    # 5. 安全替换本地 DB
    try:
        safe_replace_db(DOWNLOAD_PATH, db_path)
    except PermissionError as e:
        log.error(
            f"file lock — cannot replace {db_path}. "
            f"可能 Streamlit 仪表盘正在打开 voc.db。"
            f"请关闭 streamlit run app.py 后重跑。"
        )
        # cleanup .download so it doesn't linger
        if DOWNLOAD_PATH.exists():
            DOWNLOAD_PATH.unlink()
        return 1
    except Exception as e:
        log.error(f"replace failed: {e}")
        if DOWNLOAD_PATH.exists():
            DOWNLOAD_PATH.unlink()
        return 1

    log.info(f"[OK] local DB updated: {db_path} = {db_path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())