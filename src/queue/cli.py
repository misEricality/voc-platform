"""B 站采集队列 CLI

提供子命令：
    add BV [BV ...]              录入待采清单（自动识别 pubdate）
    list [--status X]            列出条目
    due [--limit N]              列今天到期的任务
    run-due [--limit N] [--dry-run]  触发今天的采集
    skip BV --reason X           跳过某个 BV
    remove BV                    删除条目
    show BV                      显示某个 BV 的详情

用法：
    python -m src.queue add BV1UpwaeNESx BV1xxxxxxxx
    python -m src.queue list --status pending
    python -m src.queue due
    python -m src.queue run-due --limit 10 --dry-run

最后更新：2026-08-23
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, update  # noqa: E402

from src.storage.db import (  # noqa: E402
    BilibiliQueue,
    _utcnow,
    init_db,
)


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.queue")


def _normalize_bvid(s: str) -> str:
    """支持 BV 号 + URL 两种输入格式"""
    s = s.strip()
    if "bilibili.com" in s:
        # 提取 BV 号
        import re
        m = re.search(r"(BV[A-Za-z0-9]+)", s)
        if m:
            return m.group(1)
    return s


def _lookup_pubdate(bv_id: str) -> tuple[datetime | None, str | None]:
    """通过 B 站 view 接口识别 pubdate + title

    Returns:
        (pubdate_naive_utc, title) — 失败时返回 (None, None)
    """
    try:
        import requests
    except ImportError:
        log.warning("requests 未安装，跳过 pubdate 识别")
        return None, None

    sessdata = os.getenv("BILIBILI_SESSDATA")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
    }
    cookies = {}
    if sessdata:
        cookies["SESSDATA"] = sessdata

    try:
        r = requests.get(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bv_id}",
            headers=headers,
            cookies=cookies,
            timeout=15,
        )
        if r.status_code != 200:
            log.warning(f"  {bv_id}: HTTP {r.status_code}")
            return None, None
        d = r.json()
        if d.get("code") != 0:
            log.warning(f"  {bv_id}: API code={d.get('code')} message={d.get('message')}")
            return None, None
        data = d.get("data") or {}
        pubdate_unix = data.get("pubdate")
        title = data.get("title")
        if pubdate_unix is None:
            return None, title
        # unix → naive UTC
        pubdate_utc = datetime.fromtimestamp(int(pubdate_unix), tz=timezone.utc).replace(tzinfo=None)
        return pubdate_utc, title
    except Exception as e:  # noqa: BLE001
        log.warning(f"  {bv_id}: 识别失败 {type(e).__name__}: {e}")
        return None, None


# ==================== 子命令 ====================

def cmd_add(args):
    """录入 BV 号到待采清单，自动识别 pubdate"""
    if not args.bvs:
        print("ERROR: 请提供至少一个 BV 号")
        return 2

    _, SessionLocal = init_db()
    with SessionLocal() as s:
        added = []
        for raw in args.bvs:
            bv = _normalize_bvid(raw)
            if not bv.startswith("BV"):
                print(f"  [SKIP] {raw} 格式无效")
                continue

            # 检查是否已存在
            existing = s.execute(select(BilibiliQueue).where(BilibiliQueue.bv_id == bv)).scalar_one_or_none()
            if existing is not None:
                print(f"  [EXISTS] {bv} status={existing.status}")
                continue

            # 识别 pubdate
            pubdate, title = _lookup_pubdate(bv)
            if pubdate:
                due = pubdate + timedelta(days=7)
                status = "scheduled"
            else:
                due = None
                status = "pending"

            row = BilibiliQueue(
                bv_id=bv,
                title=title,
                pubdate=pubdate,
                due_date=due,
                status=status,
                added_by="manual",
                note=args.note,
            )
            s.add(row)
            s.flush()
            print(f"  [OK] {bv} | {title or '(no title)'} | status={status} due={due}")
            added.append(bv)

        s.commit()

    print()
    print(f"新增 {len(added)} 个待采条目")
    return 0


def cmd_list(args):
    """列出条目"""
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        stmt = select(BilibiliQueue).order_by(BilibiliQueue.id.desc())
        if args.status:
            stmt = stmt.where(BilibiliQueue.status == args.status)
        rows = list(s.execute(stmt).scalars())

    print(f"=== bilibili_queue（{len(rows)} 条）===")
    print(f"{'id':>4}  {'bv_id':<15}  {'status':<10}  {'pubdate':<11}  {'due_date':<11}  {'fetched_at':<11}  {'comments':>8}  {'danmaku':>7}  title")
    print("-" * 130)
    for r in rows:
        pub = r.pubdate.strftime("%Y-%m-%d") if r.pubdate else "-"
        due = r.due_date.strftime("%Y-%m-%d") if r.due_date else "-"
        fet = r.fetched_at.strftime("%Y-%m-%d") if r.fetched_at else "-"
        title = (r.title or "")[:40]
        print(f"{r.id:>4}  {r.bv_id:<15}  {r.status:<10}  {pub:<11}  {due:<11}  {fet:<11}  {r.comment_count or 0:>8}  {r.danmaku_count or 0:>7}  {title}")

    # 统计
    print()
    print("按状态统计：")
    from collections import Counter
    counter = Counter(r.status for r in rows)
    for status, count in sorted(counter.items()):
        print(f"  {status:<12} {count}")
    return 0


def cmd_due(args):
    """列今天到期的任务"""
    _, SessionLocal = init_db()
    today = datetime.now(timezone.utc).date()
    with SessionLocal() as s:
        stmt = (
            select(BilibiliQueue)
            .where(BilibiliQueue.status == "scheduled")
            .where(BilibiliQueue.due_date <= datetime.combine(today, datetime.min.time()).replace(tzinfo=None))
            .order_by(BilibiliQueue.due_date)
            .limit(args.limit)
        )
        rows = list(s.execute(stmt).scalars())

    print(f"=== 今天到期的任务（{today}）===")
    print(f"共 {len(rows)} 条（limit={args.limit}）")
    for r in rows:
        print(f"  [{r.id}] {r.bv_id} | {r.title or '-'} | due={r.due_date.strftime('%Y-%m-%d')}")
    return 0


def cmd_run_due(args):
    """触发今天的采集"""
    from src.queue.runner import run_due_collection  # lazy import

    report = run_due_collection(
        limit=args.limit,
        dry_run=args.dry_run,
    )

    print()
    print("=== run-due report ===")
    print(f"  due found:    {report['due_found']}")
    print(f"  fetched:      {report['fetched']}")
    print(f"  failed:       {report['failed']}")
    print(f"  skipped:      {report['skipped']}")
    if report.get("errors"):
        print("  errors:")
        for e in report["errors"]:
            print(f"    - {e}")
    return 0 if report["failed"] == 0 else 1


def cmd_skip(args):
    """跳过某个 BV（标记 failed 并附原因）"""
    bv = _normalize_bvid(args.bv)
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        row = s.execute(select(BilibiliQueue).where(BilibiliQueue.bv_id == bv)).scalar_one_or_none()
        if row is None:
            print(f"ERROR: {bv} 不在队列中")
            return 2
        row.status = "failed"
        row.fail_count = (row.fail_count or 0) + 1
        row.fail_reason = args.reason or "manually skipped"
        s.commit()
    print(f"[OK] {bv} → failed ({args.reason})")
    return 0


def cmd_remove(args):
    """删除条目（仅当 status in pending/scheduled/failed）"""
    bv = _normalize_bvid(args.bv)
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        row = s.execute(select(BilibiliQueue).where(BilibiliQueue.bv_id == bv)).scalar_one_or_none()
        if row is None:
            print(f"ERROR: {bv} 不在队列中")
            return 2
        if row.status in ("fetched",):
            print(f"ERROR: {bv} 已被采集（status=fetched），不允许删除")
            return 2
        s.delete(row)
        s.commit()
    print(f"[OK] 删除 {bv}")
    return 0


def cmd_show(args):
    """显示某个 BV 的详情"""
    bv = _normalize_bvid(args.bv)
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        row = s.execute(select(BilibiliQueue).where(BilibiliQueue.bv_id == bv)).scalar_one_or_none()
        if row is None:
            print(f"ERROR: {bv} 不在队列中")
            return 2
    d = row.to_dict()
    print(json.dumps(d, ensure_ascii=False, indent=2))
    return 0


# ==================== argparse ====================

def main():
    p = argparse.ArgumentParser(description="B 站采集队列管理 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="录入 BV 号")
    p_add.add_argument("bvs", nargs="+", help="BV 号或 BV URL（可多个）")
    p_add.add_argument("--note", help="备注")

    p_list = sub.add_parser("list", help="列出条目")
    p_list.add_argument("--status", help="过滤 status（pending/scheduled/fetching/fetched/failed）")

    p_due = sub.add_parser("due", help="列今天到期的")
    p_due.add_argument("--limit", type=int, default=50)

    p_run = sub.add_parser("run-due", help="触发今天的采集")
    p_run.add_argument("--limit", type=int, default=50)
    p_run.add_argument("--dry-run", action="store_true")

    p_skip = sub.add_parser("skip", help="跳过某个 BV")
    p_skip.add_argument("bv")
    p_skip.add_argument("--reason", default="manually skipped")

    p_rm = sub.add_parser("remove", help="删除条目")
    p_rm.add_argument("bv")

    p_show = sub.add_parser("show", help="显示详情")
    p_show.add_argument("bv")

    args = p.parse_args()

    cmds = {
        "add": cmd_add,
        "list": cmd_list,
        "due": cmd_due,
        "run-due": cmd_run_due,
        "skip": cmd_skip,
        "remove": cmd_remove,
        "show": cmd_show,
    }
    return cmds[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())