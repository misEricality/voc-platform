"""一次性脚本：归档 4 款 Steam 网游数据（PUBG/Apex/Dota 2/CS2）

目标：
- 把这 4 款网游的 comments / comment_opinions / comment_embeddings / target rows
  从主 DB（data/voc.db）抽出到归档库（data/archive/online_games_<date>.db）
- 然后从主 DB 删除这 4 款的所有相关 rows
- 主 DB 之后只保留 6 款单机游戏

为什么归档而不是简单删除：
- 历史数据仍有分析价值（PUBG 2571 / Apex 1452 等大量评论）
- 未来想看网游口碑对比时还能访问
- 单机 vs 网游在不同题层面有意义（单机看内容质量，网游看社区/外挂/匹配）

使用：
    python scripts/ops/archive_online_games.py [--dry-run] [--date YYYY-MM-DD]

最后更新：2026-08-23
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# 4 款网游的 target_id
ARCHIVE_TARGETS = [
    'steam:578080',   # PUBG: BATTLEGROUNDS
    'steam:1172470',  # Apex Legends
    'steam:570',      # Dota 2
    'steam:730',      # Counter-Strike 2
]

# 测试占位（steam:999，1 条评论，posted_at=NULL）
# 一并归档避免在主库留尾巴
ALSO_ARCHIVE = ['steam:999']


def main():
    p = argparse.ArgumentParser(description='归档 Steam 网游数据')
    p.add_argument('--dry-run', action='store_true', help='只统计不执行')
    p.add_argument('--date', default=datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                   help='归档 DB 文件名后缀（默认今日 UTC）')
    args = p.parse_args()

    main_db = ROOT / 'data' / 'voc.db'
    archive_dir = ROOT / 'data' / 'archive'
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_db = archive_dir / f'online_games_{args.date}.db'

    targets_all = ARCHIVE_TARGETS + ALSO_ARCHIVE
    print(f'主 DB:    {main_db}')
    print(f'归档 DB:  {archive_db}')
    print(f'目标 ({len(targets_all)} 个): {", ".join(targets_all)}')
    print()

    if archive_db.exists() and not args.dry_run:
        print(f'ERROR: 归档 DB 已存在 {archive_db}，请先删或换 --date')
        return 1

    main = sqlite3.connect(str(main_db))
    main.row_factory = sqlite3.Row
    cur = main.cursor()

    # ---------- 0. 统计 ----------
    print('=== 0. 待归档数据统计 ===')
    placeholders = ','.join('?' * len(targets_all))
    cur.execute(f'SELECT COUNT(*) FROM comments WHERE target_id IN ({placeholders})', targets_all)
    n_comments = cur.fetchone()[0]
    cur.execute(f'SELECT COUNT(*) FROM comment_opinions WHERE comment_id IN (SELECT id FROM comments WHERE target_id IN ({placeholders}))', targets_all)
    n_opinions = cur.fetchone()[0]
    cur.execute(f'SELECT COUNT(*) FROM comment_embeddings WHERE comment_id IN (SELECT id FROM comments WHERE target_id IN ({placeholders}))', targets_all)
    n_embeds = cur.fetchone()[0]

    print(f'  comments:           {n_comments}')
    print(f'  comment_opinions:   {n_opinions}')
    print(f'  comment_embeddings: {n_embeds}')

    # 按 target 拆分
    cur.execute(f'''
        SELECT target_id, COUNT(*) as n,
               MIN(posted_at) as earliest, MAX(posted_at) as latest,
               SUM(CASE WHEN analyzed_at IS NOT NULL THEN 1 ELSE 0 END) as annotated
        FROM comments WHERE target_id IN ({placeholders})
        GROUP BY target_id ORDER BY target_id
    ''', targets_all)
    print()
    print('  按 target:')
    for row in cur.fetchall():
        print(f'    {row["target_id"]:<18} | {row["n"]:>5} 条 | 标注 {row["annotated"]:>5} | {row["earliest"]} -> {row["latest"]}')

    if args.dry_run:
        print()
        print('--dry-run: 不实际执行')
        main.close()
        return 0

    # ---------- 1. 建归档 DB + 复制 schema ----------
    print()
    print('=== 1. 建归档 DB + 复制 schema ===')
    archive = sqlite3.connect(str(archive_db))
    archive.row_factory = sqlite3.Row

    # 复制 schema（从主 DB 的 sqlite_master 拿 DDL）
    cur.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL")
    for r in cur.fetchall():
        try:
            archive.execute(r['sql'])
        except sqlite3.OperationalError as e:
            print(f'  WARN: DDL {r["name"]} 失败: {e}')
    archive.commit()
    print(f'  OK: schema 已复制')

    # ---------- 2. 拷贝数据 ----------
    print()
    print('=== 2. 拷贝 4 款网游数据到归档 DB ===')

    def copy_table(table: str, where_sql: str, where_params: tuple) -> int:
        cur.execute(f'SELECT * FROM {table} {where_sql}', where_params)
        rows = cur.fetchall()
        if not rows:
            return 0
        cols = rows[0].keys()
        col_list = ', '.join(cols)
        placeholders = ', '.join('?' * len(cols))
        archive.executemany(
            f'INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})',
            [tuple(r[c] for c in cols) for r in rows]
        )
        return len(rows)

    n_c = copy_table('comments', f'WHERE target_id IN ({placeholders})', tuple(targets_all))
    print(f'  comments:           {n_c}')

    # opinions/embeddings 用子查询（在 archive 端执行时 target_id 还没复制完）
    archive_cur = archive.cursor()
    archive_cur.execute(f'SELECT id FROM comments WHERE target_id IN ({placeholders})', tuple(targets_all))
    archived_comment_ids = [r[0] for r in archive_cur.fetchall()]
    print(f'  archived comment_ids: {len(archived_comment_ids)}')

    if archived_comment_ids:
        ph = ','.join('?' * len(archived_comment_ids))
        n_o = copy_table('comment_opinions', f'WHERE comment_id IN ({ph})', tuple(archived_comment_ids))
        print(f'  comment_opinions:   {n_o}')
        n_e = copy_table('comment_embeddings', f'WHERE comment_id IN ({ph})', tuple(archived_comment_ids))
        print(f'  comment_embeddings: {n_e}')

    archive.commit()
    archive.close()
    print(f'  OK: 归档 DB 写入完成 ({archive_db.stat().st_size / 1024 / 1024:.1f} MB)')

    # ---------- 3. 从主 DB 删除 ----------
    print()
    print('=== 3. 从主 DB 删除 4 款网游数据 ===')
    # 先删依赖表（opinions/embeddings），再删 comments
    if archived_comment_ids:
        ph = ','.join('?' * len(archived_comment_ids))
        cur.execute(f'DELETE FROM comment_opinions WHERE comment_id IN ({ph})', archived_comment_ids)
        print(f'  删除 comment_opinions: {cur.rowcount}')
        cur.execute(f'DELETE FROM comment_embeddings WHERE comment_id IN ({ph})', archived_comment_ids)
        print(f'  删除 comment_embeddings: {cur.rowcount}')

    cur.execute(f'DELETE FROM comments WHERE target_id IN ({placeholders})', targets_all)
    print(f'  删除 comments: {cur.rowcount}')

    main.commit()
    main.close()

    # ---------- 4. 主 DB VACUUM（回收空间） ----------
    print()
    print('=== 4. 主 DB VACUUM（回收空间）===')
    main2 = sqlite3.connect(str(main_db))
    main2.execute('VACUUM')
    main2.close()
    print(f'  OK: 主 DB 现 {main_db.stat().st_size / 1024 / 1024:.1f} MB')

    print()
    print('=== 归档完成 ===')
    print(f'  归档 DB:  {archive_db}')
    print(f'  归档表:   comments={n_c}, opinions={n_o}, embeddings={n_e}')
    print()
    print('下一步：')
    print('  1. 跑 scripts/smoke_test.py 确认主 DB 完好')
    print('  2. 更新 DEVELOPMENT_PLAN.md §三 数据快照：8 款 Steam → 6 款单机')
    print('  3. 更新 targets.yaml 加 excluded 段说明（防御性）')
    print('  4. CS2 专用 dev 脚本顶部加注释指向归档 DB')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())