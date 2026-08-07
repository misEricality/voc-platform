"""DB schema 迁移：comment_opinions 表 v1 → v2（2026-08-05 一次性）

v1: label / label_level / quote / quote_start / quote_end
v2: full_path / sentiment / quote / quote_start / quote_end

策略：删旧表重建（因为全量重打会重新生成 opinions，旧数据无保留价值）。
SQLite 不支持 ALTER TABLE 改列，直接 DROP + CREATE。

注意：运行前确认不需要旧 opinions 数据（已备份或即将重打）。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def main() -> None:
    conn = sqlite3.connect("data/voc.db")
    cur = conn.cursor()

    # 1. 确认旧表存在
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='comment_opinions'")
    if not cur.fetchone():
        print("comment_opinions 表不存在，跳过")
        conn.close()
        return

    # 2. 统计旧数据
    cur.execute("SELECT COUNT(*) FROM comment_opinions")
    old_count = cur.fetchone()[0]
    print(f"旧 opinions 数据: {old_count} 条")

    # 3. 删旧表
    cur.execute("DROP TABLE comment_opinions")
    print("已删除旧表 comment_opinions")

    # 4. 新表由 SQLAlchemy create_all 自动创建（init_db 时）
    conn.commit()
    conn.close()

    # 5. 验证新表能创建
    from src.storage.db import init_db
    engine, SessionLocal = init_db()
    session = SessionLocal()
    cur2 = session.connection().connection.cursor()
    cur2.execute("SELECT sql FROM sqlite_master WHERE name='comment_opinions'")
    row = cur2.fetchone()
    print("新表结构:")
    print(row[0] if row else "创建失败!")
    session.close()
    print("\n✅ 迁移完成（旧数据已清空，等待全量重打生成新 opinions）")


if __name__ == "__main__":
    main()