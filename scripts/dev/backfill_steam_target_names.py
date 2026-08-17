"""回填 Steam 评论的 target_name（extra_meta.name）。

历史采集阶段部分 Steam 评论入库时未写入游戏名称，导致导出 xlsx 的
target_name 为空。这里用 appid -> 中文名映射补齐。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import select

from src.storage.db import Comment, init_db

APP_NAMES = {
    "730": "Counter-Strike 2",
    "570": "Dota 2",
    "578080": "PUBG: BATTLEGROUNDS",
    "1172470": "Apex Legends",
    "2358720": "黑神话：悟空",
    "1222140": "底特律：化身为人",
    "292030": "巫师 3：狂猎",
    "289070": "文明 6",
    "1903340": "光与影：33号远征队",
    "753640": "星际拓荒",
}


def main() -> None:
    engine, SessionLocal = init_db()
    session = SessionLocal()
    stmt = select(Comment).where(Comment.platform == "steam")
    comments = list(session.execute(stmt).scalars())

    updated = 0
    for c in comments:
        appid = c.target_id.removeprefix("steam:")
        name = APP_NAMES.get(appid)
        if not name:
            continue
        meta = json.loads(c.extra_meta) if c.extra_meta else {}
        if meta.get("name"):
            continue
        meta["name"] = name
        meta.setdefault("type", "game")
        c.extra_meta = json.dumps(meta, ensure_ascii=False)
        updated += 1

    session.commit()
    session.close()
    print(f"updated {updated} steam comments")


if __name__ == "__main__":
    main()
