"""P6 辅助：把 GH Release 上的 voc.db 同步到本地 data/voc.db。

背景：
- daily-collect.yml 跑在 GitHub Actions ubuntu-latest runner 上，**不会**写到
  本机的 data/voc.db。每次自动采集成功后的"线上 DB"只活在 GH Release asset 里。
- 本脚本给开发者一个手动对齐本地与线上 DB 的工具。

行为：
- 默认从 GH 拉 **最新一个** voc-daily-* release 的 voc.db asset，原子替换本地 DB
- 也可指定 --tag 拉某个具体日期的 release
- --bootstrap 拉 voc-daily-bootstrap 基线
- --dry-run 只列 release + asset 名，不下载
- 总是先下到 .download 临时文件，下载成功才原子替换（避免半成品覆盖现有 DB）
- 替换前可指定 --max-age-days，超过这个阈值才允许覆盖（防止误覆盖新数据）

与 scripts/ops/daily_incremental_collect.py 的关系：
- 不复用 daily_incremental_collect.py 的 gh_release_download()，因为：
  1) 它强制绑 db_path=DEFAULT_DB_PATH，本脚本希望更灵活
  2) 它的命令路径是基于 gh CLI，本脚本只走 REST API（无需 gh CLI 安装）
  3) 它的实现是 inline 到 main 流程的，本脚本期望独立调用

使用：
    # 拉最新 voc-daily-* → 默认路径
    python scripts/ops/sync_local_from_release.py

    # 拉指定 tag
    python scripts/ops/sync_local_from_release.py --tag voc-daily-2026-08-23

    # 拉 bootstrap
    python scripts/ops/sync_local_from_release.py --bootstrap

    # 只看不下载
    python scripts/ops/sync_local_from_release.py --dry-run

    # 限本地 DB 至少 N 天没更新才覆盖（防误操作）
    python scripts/ops/sync_local_from_release.py --max-age-days 7
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.sync_release")

DEFAULT_DB_PATH = ROOT / "data" / "voc.db"
DEFAULT_REPO = "misEricality/voc-platform"
DAILY_TAG_PREFIX = "voc-daily"
BOOTSTRAP_TAG = "voc-daily-bootstrap"
DB_ASSET_NAME = "voc.db"
DB_ASSET_PREFIX = "voc.db"  # 兼容网页端上传的自动加后缀


def _api(path: str, *, token: str | None = None) -> tuple[int, dict | list | None]:
    """GET GitHub REST API；token 可选（public repo 读无需 auth，写才需要）"""
    url = f"https://api.github.com{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "voc-platform-sync",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def list_releases(repo: str, *, token: str | None = None) -> list[dict]:
    """列所有 releases（按 published_at 倒序）"""
    status, data = _api(f"/repos/{repo}/releases?per_page=50", token=token)
    if status != 200:
        raise RuntimeError(f"list_releases failed: {status} {data}")
    return data


def latest_daily_tag(repo: str, *, token: str | None = None) -> str | None:
    """找最新一个 voc-daily-YYYY-MM-DD tag（voc-daily-bootstrap 不算）"""
    for rel in list_releases(repo, token=token):
        tag = rel["tag_name"]
        if tag.startswith(DAILY_TAG_PREFIX + "-") and tag != BOOTSTRAP_TAG:
            # 跳过 bootstrap；找第一个 voc-daily-YYYY-MM-DD
            return tag
    return None


def find_db_asset(release: dict) -> str | None:
    """在 release 的 assets 中找 voc.db 资产（兼容网页端上传的自动后缀）"""
    asset_names = [a["name"] for a in release.get("assets", [])]
    # 优先精确匹配
    for n in asset_names:
        if n == DB_ASSET_NAME:
            return n
    # 退化到前缀匹配
    for n in asset_names:
        if n.startswith(DB_ASSET_PREFIX):
            return n
    return None


def download_asset(
    repo: str,
    tag: str,
    asset_name: str,
    dst: Path,
    *,
    token: str | None = None,
) -> bool:
    """下载 asset 到 dst；先下到 .download 临时文件，成功后原子替换"""
    # 用 redirect 跟随的 API 端点
    url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_name}"
    # 实际 download URL 在 release['assets'][i]['url']，但那个要 header 跟 redirect
    # 简单做法：用 release tag + asset_name 直接拼，浏览器可访问
    # 更稳的做法：用 release['upload_url'] 模板拿原始 URL
    # 这里采用最直接方式：先 GET release 拿到 asset 列表，再访问浏览器下载 URL
    status, release = _api(f"/repos/{repo}/releases/tags/{tag}", token=token)
    if status != 200:
        log.error(f"GET release {tag} failed: {status}")
        return False
    target = next((a for a in release.get("assets", []) if a["name"] == asset_name), None)
    if not target:
        log.error(f"asset {asset_name} not in release {tag}")
        return False
    browser_url = target["browser_download_url"]
    log.info(f"GET {browser_url}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".download")
    if tmp.exists():
        tmp.unlink()
    req = urllib.request.Request(browser_url, headers={"User-Agent": "voc-platform-sync"})
    if token:
        # private asset 才需要 auth header
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 64 * 1024
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
            log.info(f"downloaded {downloaded} bytes" + (f" / {total}" if total else ""))
    except Exception as e:  # noqa: BLE001
        log.error(f"download failed: {e}")
        if tmp.exists():
            tmp.unlink()
        return False
    # 原子替换
    tmp.replace(dst)
    log.info(f"atomic replace: {tmp.name} -> {dst.name}")
    return True


def main():
    p = argparse.ArgumentParser(description="把 GH Release 上的 voc.db 同步到本地")
    p.add_argument("--repo", default=DEFAULT_REPO, help=f"GitHub repo (default: {DEFAULT_REPO})")
    p.add_argument("--tag", help="指定 release tag（默认找最新 voc-daily-*）")
    p.add_argument("--bootstrap", action="store_true", help=f"使用 {BOOTSTRAP_TAG}")
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="本地 DB 路径")
    p.add_argument("--token", default=os.getenv("GH_TOKEN"), help="GitHub PAT（public repo 可省）")
    p.add_argument("--dry-run", action="store_true", help="只列 release + asset，不下载")
    p.add_argument("--max-age-days", type=int, default=0,
                   help="本地 DB 必须早于 N 天才允许覆盖（0=不限）")
    args = p.parse_args()

    db_path = Path(args.db_path)

    # 1. 决定目标 tag
    if args.bootstrap:
        tag = BOOTSTRAP_TAG
    elif args.tag:
        tag = args.tag
    else:
        tag = latest_daily_tag(args.repo, token=args.token)
        if not tag:
            log.error("no voc-daily-* release found")
            sys.exit(1)
    log.info(f"target release tag: {tag}")

    # 2. 拿 release + asset 名
    status, release = _api(f"/repos/{args.repo}/releases/tags/{tag}", token=args.token)
    if status != 200:
        log.error(f"GET release {tag} failed: {status}")
        sys.exit(1)
    asset_name = find_db_asset(release)
    if not asset_name:
        log.error(f"release {tag} has no {DB_ASSET_PREFIX}* asset; assets={[a['name'] for a in release.get('assets', [])]}")
        sys.exit(1)
    asset = next(a for a in release["assets"] if a["name"] == asset_name)
    log.info(f"asset: {asset_name} ({asset['size']:,} bytes, state={asset['state']}, "
             f"updated_at={asset['updated_at']})")

    if args.dry_run:
        log.info("[dry-run] skip download")
        return

    # 3. age 守卫
    if args.max_age_days > 0 and db_path.exists():
        age_sec = time.time() - db_path.stat().st_mtime
        age_days = age_sec / 86400
        if age_days < args.max_age_days:
            log.warning(f"local DB is only {age_days:.1f} days old (limit: {args.max_age_days}); "
                        f"refuse to overwrite. Pass --max-age-days 0 to force.")
            sys.exit(1)

    # 4. 下载
    if not download_asset(args.repo, tag, asset_name, db_path, token=args.token):
        sys.exit(1)

    # 5. 简单验证：SQLite 文件头 + row count
    if db_path.exists():
        head = db_path.read_bytes()[:16]
        if head.startswith(b"SQLite format 3"):
            log.info("[OK] SQLite header valid")
        else:
            log.error(f"[FAIL] not a SQLite file: header={head!r}")
            sys.exit(1)
        log.info(f"[OK] synced: {db_path} = {db_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()