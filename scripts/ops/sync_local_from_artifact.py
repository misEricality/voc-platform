"""从 GH Actions run artifact 下载 DB（兼容 release asset 缺失场景）。

背景：
- scripts/ops/sync_local_from_release.py 默认从 release asset 拉
- 但 P6 release upload 有 bug（2026-08-22 memory 记录），assets=[] 但 workflow 仍 success
- 真正落库的数据在 actions/artifacts/{id}/zip (upload-artifact@v4 fallback)
- 本脚本是 artifact-only 通道

使用：
    python scripts/ops/sync_local_from_artifact.py
    python scripts/ops/sync_local_from_artifact.py --run-number 18
    python scripts/ops/sync_local_from_artifact.py --artifact-id 9527915001
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
import zipfile
from pathlib import Path

import urllib.parse

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.sync_artifact")

DEFAULT_DB_PATH = ROOT / "data" / "voc.db"
DEFAULT_REPO = "misEricality/voc-platform"
WF_NAME = "daily-collect.yml"
ARTIFACT_GLOB = "voc-db-"


def _api(path: str, *, token: str | None) -> tuple[int, dict | list | None]:
    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "voc-platform-sync",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def latest_run_id_with_artifact(
    repo: str, *, workflow_name: str, token: str, artifact_name_prefix: str
) -> tuple[int, dict]:
    """找最近一个 run 的 id 和 artifact 元信息"""
    # 1. 列 workflow runs
    status, runs = _api(
        f"/repos/{repo}/actions/workflows/{workflow_name}/runs?per_page=20", token=token
    )
    if status != 200:
        raise RuntimeError(f"list runs failed: {status}")
    for run in runs["workflow_runs"]:
        rid = run["id"]
        status, arts = _api(
            f"/repos/{repo}/actions/runs/{rid}/artifacts", token=token
        )
        if status != 200:
            continue
        for a in arts["artifacts"]:
            if a["name"].startswith(artifact_name_prefix) and not a["expired"]:
                return rid, a
    raise RuntimeError(f"no artifact matching {artifact_name_prefix}* in recent runs")


class _AuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """已废弃：artifact 下载改用 requests 实现（保留 imports 防 lint 报错）"""
    pass


def download_artifact_zip(repo: str, artifact_id: int, *, token: str) -> bytes:
    """下载 artifact zip。

    GitHub artifact download 会 302 到 Azure Blob，Authorization header 必须
    在 redirect 后保留。用 requests（不带 Authorization on redirect）的默认行为
    会丢 header → 401。改用 session + 不让 requests 自动 strip Authorization。

    实现：手动拿 Location header 后用 requests GET（带 Authorization），让 requests
    自动 follow 后续的 redirect。
    """
    import requests as _requests  # 已在 requirements-core.txt
    # 第一步：拿 Location header（不 follow redirect）
    r = _requests.get(
        f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "voc-platform-sync",
            "Accept": "application/vnd.github+json",
        },
        allow_redirects=False,
        timeout=30,
    )
    if r.status_code not in (301, 302, 303, 307):
        # 直接 200/304 = 直接返回内容（少见但要兜底）
        if r.ok:
            return r.content
        raise RuntimeError(f"unexpected status {r.status_code}: {r.text[:200]}")
    signed_url = r.headers["Location"]
    log.info(f"redirected to signed URL (host={urllib.parse.urlparse(signed_url).netloc})")
    # 第二步：用 requests 走 signed URL（不带 Authorization，让签名生效）
    r2 = _requests.get(signed_url, timeout=180, stream=False)
    r2.raise_for_status()
    return r2.content


def extract_db_from_zip(zip_bytes: bytes, expected_db_name: str = "voc.db") -> bytes:
    """从 zip 里拿出 voc.db 文件内容"""
    import io as _io
    with zipfile.ZipFile(_io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if name.endswith(expected_db_name):
                return zf.read(name)
    raise RuntimeError(f"no {expected_db_name} in zip; contents: {zf.namelist()}")


def atomic_replace(dst: Path, data: bytes) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".download")
    if tmp.exists():
        tmp.unlink()
    tmp.write_bytes(data)
    tmp.replace(dst)
    log.info(f"atomic replace: {tmp.name} -> {dst.name}")


def main():
    p = argparse.ArgumentParser(description="从 GH Actions artifact 下载 DB")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--workflow", default=WF_NAME)
    p.add_argument("--artifact-prefix", default=ARTIFACT_GLOB)
    p.add_argument("--run-number", type=int, help="指定 run number（默认最新有 artifact 的 run）")
    p.add_argument("--artifact-id", type=int, help="直接指定 artifact id")
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    p.add_argument("--token", default=os.getenv("GH_TOKEN"))
    p.add_argument("--max-age-days", type=int, default=0)
    args = p.parse_args()

    if not args.token:
        log.error("--token or $GH_TOKEN required")
        sys.exit(1)

    db_path = Path(args.db_path)

    # 1. 拿 artifact 元信息
    if args.artifact_id:
        status, art = _api(
            f"/repos/{args.repo}/actions/artifacts/{args.artifact_id}", token=args.token
        )
        if status != 200:
            raise RuntimeError(f"artifact {args.artifact_id} not found: {status}")
        artifact_id = args.artifact_id
    else:
        if args.run_number:
            # 拿该 run_number 的 artifact
            status, runs = _api(
                f"/repos/{args.repo}/actions/workflows/{args.workflow}/runs?per_page=50",
                token=args.token,
            )
            target = next((r for r in runs["workflow_runs"] if r["run_number"] == args.run_number), None)
            if not target:
                raise RuntimeError(f"run #{args.run_number} not found")
            status, arts = _api(
                f"/repos/{args.repo}/actions/runs/{target['id']}/artifacts", token=args.token
            )
            art = next(
                (a for a in arts["artifacts"] if a["name"].startswith(args.artifact_prefix)),
                None,
            )
            if not art:
                raise RuntimeError(f"run #{args.run_number} has no {args.artifact_prefix}* artifact")
            artifact_id = art["id"]
        else:
            rid, art = latest_run_id_with_artifact(
                args.repo,
                workflow_name=args.workflow,
                token=args.token,
                artifact_name_prefix=args.artifact_prefix,
            )
            artifact_id = art["id"]
            log.info(f"latest run with artifact: run_id={rid} artifact={art['name']}")

    log.info(
        f"artifact: id={artifact_id} name={art['name']} "
        f"size={art['size_in_bytes']:,} expired={art['expired']}"
    )

    # 2. age 守卫
    if args.max_age_days > 0 and db_path.exists():
        age_sec = time.time() - db_path.stat().st_mtime
        age_days = age_sec / 86400
        if age_days < args.max_age_days:
            log.warning(f"local DB is only {age_days:.1f} days old; refuse to overwrite")
            sys.exit(1)

    # 3. 下载 + 解压 + 落盘
    log.info(f"downloading artifact zip...")
    zip_bytes = download_artifact_zip(args.repo, artifact_id, token=args.token)
    log.info(f"zip downloaded: {len(zip_bytes):,} bytes")
    db_bytes = extract_db_from_zip(zip_bytes, expected_db_name="voc.db")
    log.info(f"DB extracted: {len(db_bytes):,} bytes")
    atomic_replace(db_path, db_bytes)

    # 4. 验证
    head = db_path.read_bytes()[:16]
    if head.startswith(b"SQLite format 3"):
        log.info("[OK] SQLite header valid")
    else:
        log.error(f"[FAIL] not a SQLite file: header={head!r}")
        sys.exit(1)
    log.info(f"[OK] synced: {db_path} = {db_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()