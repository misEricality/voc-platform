"""导出「原声列表」高保真原型所需的真实数据 JSON

输出内容（单个 JSON）：
- games:   已采集游戏列表（appid + 名称 + 评论数）
- tags:    L1~L3 标签树（来自 config/topics/gaming.yaml，附各层命中计数）
- voices:  评论全字段（含观点列表、Steam 扩展字段）

用法：
    python scripts/dev/export_prototype_data.py
    # 默认读取 data/voc.db，输出 data/exports/prototype_voices.json
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "voc.db"
TOPICS_PATH = ROOT / "config" / "topics" / "gaming.yaml"
OUT_PATH = ROOT / "data" / "exports" / "prototype_voices.json"


def load_tag_tree() -> dict:
    with open(TOPICS_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg["hierarchy"]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ---- 游戏列表 ----
    games: dict[str, dict] = {}
    for row in cur.execute(
        "SELECT target_id, COUNT(*) AS n, MIN(extra_meta) AS meta FROM comments GROUP BY target_id"
    ):
        appid = row["target_id"].split(":", 1)[1]
        name = appid
        if row["meta"]:
            try:
                name = json.loads(row["meta"]).get("name") or appid
            except json.JSONDecodeError:
                pass
        games[appid] = {"appid": appid, "name": name, "count": row["n"]}

    # ---- 观点（按评论聚合）----
    opinions_by_comment: dict[int, list[dict]] = {}
    for row in cur.execute(
        "SELECT comment_id, full_path, sentiment, quote FROM comment_opinions ORDER BY id"
    ):
        opinions_by_comment.setdefault(row["comment_id"], []).append(
            {"path": row["full_path"], "sentiment": row["sentiment"], "quote": row["quote"]}
        )

    # ---- 标签树计数 ----
    # L1: comments.topic；L2: comments.sub_topics；L3: comment_opinions.full_path
    l1_counts: dict[str, int] = {}
    l2_counts: dict[str, int] = {}
    l3_counts: dict[str, int] = {}
    for row in cur.execute("SELECT topic, sub_topics FROM comments"):
        if row["topic"]:
            l1_counts[row["topic"]] = l1_counts.get(row["topic"], 0) + 1
        if row["sub_topics"]:
            for l2 in json.loads(row["sub_topics"]):
                l2_counts[l2] = l2_counts.get(l2, 0) + 1
    for row in cur.execute("SELECT full_path FROM comment_opinions"):
        parts = row["full_path"].split("/")
        if len(parts) >= 3:
            key = "/".join(parts[:3])
            l3_counts[key] = l3_counts.get(key, 0) + 1

    hierarchy = load_tag_tree()
    tag_tree = []
    for l1, l2_map in hierarchy.items():
        l2_nodes = []
        for l2, l3_list in l2_map.items():
            l3_nodes = [
                {"name": l3, "count": l3_counts.get(f"{l1}/{l2}/{l3}", 0)}
                for l3 in l3_list
            ]
            l2_nodes.append({"name": l2, "count": l2_counts.get(l2, 0), "children": l3_nodes})
        tag_tree.append({"name": l1, "count": l1_counts.get(l1, 0), "children": l2_nodes})

    # ---- 评论全字段 ----
    voices = []
    for row in cur.execute("SELECT * FROM comments ORDER BY posted_at DESC"):
        extra = {}
        if row["extra_json"]:
            try:
                extra = json.loads(row["extra_json"])
            except json.JSONDecodeError:
                pass
        appid = str(extra.get("appid") or row["target_id"].split(":", 1)[1])
        voices.append(
            {
                "id": row["id"],
                "appid": appid,
                "game": games.get(appid, {}).get("name", appid),
                "content": row["content"],
                "author": row["author"],
                "rating": row["rating"],
                "language": row["language"],
                "posted_at": row["posted_at"],
                "sentiment": row["sentiment"],
                "sentiment_score": row["sentiment_score"],
                "sentiment_confidence": row["sentiment_confidence"],
                "topic": row["topic"],
                "sub_topics": json.loads(row["sub_topics"]) if row["sub_topics"] else [],
                "likes": row["likes"],
                "replies": row["replies"],
                "likes_refreshed_at": row["likes_refreshed_at"],
                "playtime_at_review": extra.get("playtime_at_review"),
                "playtime_forever": extra.get("playtime_forever"),
                "refunded": bool(extra.get("refunded")),
                "early_access": bool(extra.get("written_during_early_access")),
                "steam_deck": bool(extra.get("primarily_steam_deck")),
                "received_for_free": bool(extra.get("received_for_free")),
                "weighted_vote_score": extra.get("weighted_vote_score"),
                "source_id": row["source_id"],
                "opinions": opinions_by_comment.get(row["id"], []),
            }
        )

    payload = {
        "games": sorted(games.values(), key=lambda g: g["count"], reverse=True),
        "tags": tag_tree,
        "voices": voices,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"voices={len(voices)} games={len(games)} opinions_comments={len(opinions_by_comment)}")
    print(f"-> {OUT_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    sys.exit(main())
