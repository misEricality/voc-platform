"""P6 release upload 静默失败防御：校验当天 GH Release asset 是否成功上传

背景：
- scripts/ops/daily_incremental_collect.py 的 gh_release_upload() 失败仅记 warning
- workflow 标 success → 远端 artifact 正常但 release assets=[]（P6 A1 历史 bug）
- 累积 DB 核心目标实际未生效（assets=[]）
- 见 docs/architecture/AUTOMATION_PIPELINE.md §8.3

作用：
- workflow 「每日采集 + 上传」步骤后立即跑本脚本
- 用 gh release view 查 release 的 assets 列表
- 校验 voc.db* asset 存在 + state=uploaded + size > --min-size（默认 1024 bytes）
- 任何条件不满足 → exit 1 → workflow 步骤标 ❌ → GH 邮件告警

设计：
- 默认 tag = voc-daily-{today UTC}（对齐 daily_incremental_collect.today_tag）
- 可 --tag 指定任意 tag 做手动验证
- --min-size 默认 1024 bytes；远小于真实 voc.db（>= 数十 KB）
- 用 gh CLI 而非 REST API，跟其它 ops/ 脚本一致（且 public repo 无需 token）
- 输出结构化日志，方便 Actions summary 抓取

使用：
    # workflow 默认行为（默认值即可）
    python scripts/ops/verify_release_upload.py

    # 手动验证特定 tag
    python scripts/ops/verify_release_upload.py --tag voc-daily-2026-08-27

    # 自定义最小字节阈值
    python scripts/ops/verify_release_upload.py --min-size 102400
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.storage.db import _utcnow  # noqa: E402  复用项目时间函数保持一致

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.verify_release")


DB_ASSET_PREFIX = "voc.db"
DEFAULT_REPO = "misEricality/voc-platform"
DEFAULT_TAG_PREFIX = "voc-daily"
DEFAULT_MIN_SIZE = 1024  # 1 KB；远小于真实 DB（数十 KB～数十 MB）


def today_tag(prefix: str = DEFAULT_TAG_PREFIX) -> str:
    """生成今日 tag：voc-daily-YYYY-MM-DD（UTC，跟 daily_incremental_collect.today_tag 对齐）"""
    return f"{prefix}-{_utcnow().strftime('%Y-%m-%d')}"


def gh_release_get(tag: str) -> dict | None:
    """用 gh CLI 拿 release JSON；失败返回 None

    Returns:
        dict: release JSON（含 assets 列表）；None 表示 release 不存在 / API 失败 / gh CLI 缺失

    Note:
        FileNotFoundError（gh 不在 PATH）由调用方 `verify()` 处理并返回友好 message，
        否则 gh 缺失跟 release 缺失无法区分。
    """
    r = subprocess.run(
        ["gh", "release", "view", tag, "--json", "tagName,name,assets,isDraft"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        log.warning(f"gh release view {tag} 失败：{r.stderr.strip()}")
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        log.warning(f"解析 release JSON 失败：{e}")
        return None


def find_db_asset(release: dict) -> dict | None:
    """从 release.assets 中找 voc.db* asset（兼容网页端上传时 -1/-2 后缀）

    Returns:
        dict: asset JSON（含 name/size/state 等）；None 表示未找到
    """
    assets = release.get("assets", []) or []
    # 优先精确匹配 voc.db，fallback 到前缀匹配
    for a in assets:
        if a.get("name") == "voc.db":
            return a
    for a in assets:
        if a.get("name", "").startswith(DB_ASSET_PREFIX):
            return a
    return None


def verify(tag: str, *, min_size: int = DEFAULT_MIN_SIZE) -> tuple[bool, str]:
    """校验 release 是否成功上传 voc.db asset。

    Returns:
        (ok: bool, message: str) — ok=True 表示校验通过；False 表示失败并给出原因
    """
    log.info(f"verify target tag: {tag} (min_size={min_size:,} bytes)")

    try:
        release = gh_release_get(tag)
    except FileNotFoundError:
        msg = (
            "gh CLI 不在 PATH 中；本脚本必须在 GitHub Actions runner 上运行"
            "（ubuntu-latest 已预装）"
        )
        log.error(msg)
        return False, msg

    if release is None:
        return False, f"release {tag} 不存在或获取失败（可能是首次跑当天还没 create；查 log）"

    if release.get("isDraft"):
        log.warning(f"release {tag} 是 draft 状态——assets 可能未 publish")

    asset = find_db_asset(release)
    if asset is None:
        asset_names = [a.get("name") for a in (release.get("assets") or [])]
        return False, f"release {tag} 下未找到 {DB_ASSET_PREFIX}* asset（现有：{asset_names}）"

    name = asset.get("name")
    size = asset.get("size", 0) or 0
    state = asset.get("state", "unknown")
    log.info(f"asset found: name={name} size={size:,} state={state}")

    if state != "uploaded":
        return False, f"asset {name} state={state}（应为 uploaded）"

    if size < min_size:
        return False, (
            f"asset {name} size={size:,} bytes < min_size={min_size:,} bytes "
            f"—— 上传可能不完整"
        )

    log.info(
        f"[OK] release {tag} asset {name} 已上传："
        f"{size:,} bytes / state={state}"
    )
    return True, f"release {tag} asset {name} OK ({size:,} bytes, state={state})"


def main() -> int:
    p = argparse.ArgumentParser(
        description="校验 GH Release asset 是否成功上传（P6 静默失败防御）"
    )
    p.add_argument(
        "--tag",
        default=today_tag(),
        help=f"release tag（默认：{DEFAULT_TAG_PREFIX}-{{today UTC}}）",
    )
    p.add_argument(
        "--min-size",
        type=int,
        default=DEFAULT_MIN_SIZE,
        help=f"asset 最小字节数（默认 {DEFAULT_MIN_SIZE}）",
    )
    p.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"GitHub repo（默认 {DEFAULT_REPO}，目前仅显示用）",
    )
    args = p.parse_args()

    ok, msg = verify(args.tag, min_size=args.min_size)
    if ok:
        log.info(f"[PASS] {msg}")
        return 0
    log.error(f"[FAIL] {msg}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
