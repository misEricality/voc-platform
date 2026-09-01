"""复现 reanalyze_all.py 的随机抽样逻辑，保存 500 条评论 ID。

用于：重打前固定样本，重打后按同一批 ID 导出 xlsx。
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import select

from src.storage.db import Comment, init_db

OUT = Path("data/validation/_random500_ids.json")
LIMIT = 500


def main() -> None:
    engine, SessionLocal = init_db()
    session = SessionLocal()
    stmt = select(Comment).order_by(Comment.fetched_at)
    comments = list(session.execute(stmt).scalars())

    random.seed(42)
    sample = random.sample(comments, LIMIT)
    ids = [c.id for c in sample]

    OUT.write_text(
        json.dumps(ids, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"selected {len(ids)} comments -> {OUT}")
    session.close()


if __name__ == "__main__":
    main()
