"""一键验证今天的 workflow 跑通后本地数据状态

跑这个脚本前需要：
1. workflow 已 dispatch 成功（远端 release voc-daily-YYYY-MM-DD 已上传）
2. 关闭 Streamlit（释放 voc.db 文件锁）

输出：
- sync 状态（本地 DB 是否最新）
- 今天入库数据的 posted_at 分布
- analyzer_version 分布（关键：应该看到 llm:glm-5.3-flash@xxx）
- 情感分布
- 6 款游戏的 fetched/analyzed 数

用法：.\.venv-ml\Scripts\python.exe scripts/dev/verify_today_collect.py [UTC日期]
   默认今天 UTC（workflow 跑时间对应）
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "voc.db"


def section(title: str) -> None:
    print()
    print("=" * 60)
    print(f" {title}")
    print("=" * 60)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="UTC 日期（默认今天 UTC）", default=None)
    p.add_argument("--skip-sync", action="store_true", help="跳过 sync_release（已手动同步过）")
    args = p.parse_args()

    target_date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"=== 验证目标日期 (UTC): {target_date}")
    print(f"=== DB 路径: {DB}")

    # ---- Step 1: sync GH Release → 本地 DB ----
    section("Step 1 · Sync GH Release → 本地 DB")
    if args.skip_sync:
        print("[skip] --skip-sync，跳过 sync")
    else:
        print("检查 Streamlit 是否在跑（必须先关）...")
        try:
            r = subprocess.run(
                ["powershell", "-Command", "Get-Process streamlit -ErrorAction SilentlyContinue"],
                capture_output=True, text=True, timeout=5,
            )
            if "streamlit" in r.stdout.lower():
                print("[WARN] Streamlit 正在运行！请先关掉再 sync（否则 safe_replace_db 会失败）")
                print("    命令: Get-Process streamlit | Stop-Process -Force")
                return 1
            print("[OK] Streamlit 未运行")
        except Exception as e:
            print(f"[warn] 无法检测 streamlit 进程: {e}")

        print(f"\n调用 smart_sync_release.py 同步今日 release...")
        r = subprocess.run(
            [sys.executable, "scripts/ops/smart_sync_release.py", "--date", target_date],
            capture_output=True, text=True, timeout=120,
        )
        # 打印最后几行日志
        for line in r.stdout.splitlines()[-10:]:
            print(f"  {line}")
        if r.returncode != 0:
            print(f"[FAIL] sync 失败 (exit={r.returncode})")
            print(r.stderr)
            return 1
        print("[OK] sync 成功")

    # ---- Step 2: 验证 DB ----
    section("Step 2 · DB 内容验证")
    if not DB.exists():
        print(f"[FAIL] DB 不存在: {DB}")
        return 1
    print(f"[OK] DB 大小: {DB.stat().st_size:,} bytes")

    conn = sqlite3.connect(str(DB))
    c = conn.cursor()

    # 2.1 今日入库数据按 posted_at 分布
    print("\n--- 今日入库评论按 posted_at 分布 ---")
    c.execute("""
        SELECT DATE(posted_at) AS d, COUNT(*) AS cnt
        FROM comments
        WHERE DATE(fetched_at) = ?
        GROUP BY DATE(posted_at)
        ORDER BY d
    """, (target_date,))
    rows = c.fetchall()
    if not rows:
        print(f"[WARN] 今日 (UTC {target_date}) 无入库数据")
    else:
        for d, cnt in rows:
            tag = ""
            # v2 时间窗：应该集中在「北京昨天 + 北京前天」= UTC target_date 前 1-2 天
            # 简单判断：今天不应该出现
            td = datetime.strptime(target_date, "%Y-%m-%d").date()
            if d == target_date:
                tag = "  <- 当天（违反 v2 '不采当天' 语义）"
            elif d == (td - timedelta(days=1)).isoformat():
                tag = "  <- 昨天（主采集目标）"
            elif d == (td - timedelta(days=2)).isoformat():
                tag = "  <- 前天（补采目标）"
            print(f"  {d}: {cnt:>4} 条{tag}")

    # 2.2 analyzer_version 分布（关键）
    print("\n--- 今日已分析评论按 analyzer_version 分布 ---")
    c.execute("""
        SELECT analyzer_version, COUNT(*) AS cnt
        FROM comments
        WHERE DATE(fetched_at) = ? AND analyzed_at IS NOT NULL
        GROUP BY analyzer_version
        ORDER BY cnt DESC
    """, (target_date,))
    rows = c.fetchall()
    if not rows:
        print(f"[WARN] 今日无已分析评论")
    for ver, cnt in rows:
        marker = " [OK] GLM" if "glm-5.3-flash" in ver else ""
        marker += " [v2 默认标注器]" if "glm-5.3-flash" in ver else ""
        marker += " [WARN] 回退到旧 provider" if "deepseek" in ver or "glm" == ver.split(":")[1].split("@")[0] else ""
        print(f"  {ver}: {cnt} 条{marker}")

    # 2.3 按 target 看 fetched / analyzed
    print("\n--- 6 款游戏今日采集统计 ---")
    c.execute("""
        SELECT target_id,
               COUNT(*) AS total,
               SUM(CASE WHEN analyzed_at IS NOT NULL THEN 1 ELSE 0 END) AS analyzed
        FROM comments
        WHERE DATE(fetched_at) = ?
        GROUP BY target_id
        ORDER BY target_id
    """, (target_date,))
    for tid, total, analyzed in c.fetchall():
        rate = f"{analyzed/total*100:.1f}%" if total else "—"
        print(f"  {tid}: total={total}, analyzed={analyzed} ({rate})")

    # 2.4 情感分布
    print("\n--- 今日评论情感分布 ---")
    c.execute("""
        SELECT sentiment, COUNT(*) AS cnt
        FROM comments
        WHERE DATE(fetched_at) = ?
        GROUP BY sentiment
        ORDER BY cnt DESC
    """, (target_date,))
    for senti, cnt in c.fetchall():
        print(f"  {senti or 'NULL'}: {cnt} 条")

    # 2.5 当天数据检查（v2 应该 0 条）
    print("\n--- v2 '当天不采' 语义校验 ---")
    c.execute("""
        SELECT COUNT(*) FROM comments
        WHERE DATE(fetched_at) = ? AND DATE(posted_at) = ?
    """, (target_date, target_date))
    same_day_count = c.fetchone()[0]
    if same_day_count == 0:
        print(f"  [OK] 当天 (UTC {target_date}) 数据 0 条（v2 时间窗生效）")
    else:
        print(f"  [WARN] 当天 (UTC {target_date}) 有 {same_day_count} 条数据（违反 v2 语义）")

    # ---- Step 3: 总结 ----
    section("Step 3 · 总评")
    c.execute("""
        SELECT COUNT(*) FROM comments
        WHERE DATE(fetched_at) = ? AND analyzer_version LIKE 'llm:glm-5.3-flash@%'
    """, (target_date,))
    glm_count = c.fetchone()[0]
    print(f"GLM-5.3-Flash 标注写入: {glm_count} 条")
    if glm_count > 0 and same_day_count == 0:
        print("[OK] 全部验证通过！")
        print("  - 主标注器已切到 GLM-5.3-Flash")
        print("  - 时间窗 v2 生效（不采当天）")
        return 0
    else:
        print("[WARN] 验证未完全通过，看上面提示排查")
        return 1


if __name__ == "__main__":
    sys.exit(main())