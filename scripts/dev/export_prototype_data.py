"""导出高保真原型所需的真实数据 JSON（仅 Steam 平台）

输出内容（单个 JSON）：
- games:     Steam 已采集游戏列表（appid + 名称 + 评论数）
- tags:      L1~L3 标签树（来自 config/topics/gaming.yaml，附各层命中计数，仅 Steam）
- voices:    Steam 评论全字段（原声数据单元，含观点列表、Steam 扩展字段）
- opinions:  Steam 观点数据单元（独立数组，含关联评论字段 + 观点情感）

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

PLATFORM = "steam"


def load_tag_tree() -> dict:
    with open(TOPICS_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg["hierarchy"]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ---- Steam 游戏列表 ----
    games: dict[str, dict] = {}
    for row in cur.execute(
        "SELECT target_id, COUNT(*) AS n, MIN(extra_meta) AS meta FROM comments "
        "WHERE platform=? GROUP BY target_id", (PLATFORM,)
    ):
        appid = row["target_id"].split(":", 1)[1]
        name = appid
        if row["meta"]:
            try:
                name = json.loads(row["meta"]).get("name") or appid
            except json.JSONDecodeError:
                pass
        games[appid] = {"appid": appid, "name": name, "count": row["n"]}

    # ---- Steam 评论全字段（内存建索引，供观点单元关联） ----
    comment_index: dict[int, dict] = {}
    voices: list[dict] = []
    opinions_by_comment: dict[int, list[dict]] = {}
    for row in cur.execute(
        "SELECT * FROM comments WHERE platform=? ORDER BY posted_at DESC", (PLATFORM,)
    ):
        extra = {}
        if row["extra_json"]:
            try:
                extra = json.loads(row["extra_json"])
            except json.JSONDecodeError:
                pass
        appid = str(extra.get("appid") or row["target_id"].split(":", 1)[1])
        c = {
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
            "opinions": [],
        }
        comment_index[row["id"]] = c
        voices.append(c)

    # ---- Steam 观点（独立数据单元 + 按评论聚合） ----
    opinions: list[dict] = []
    for row in cur.execute(
        "SELECT op.* FROM comment_opinions op JOIN comments c ON op.comment_id=c.id "
        "WHERE c.platform=? ORDER BY op.id", (PLATFORM,)
    ):
        c = comment_index.get(row["comment_id"])
        if c is None:
            continue
        op = {
            "id": row["id"],
            "comment_id": row["comment_id"],
            "path": row["full_path"],
            "sentiment": row["sentiment"],
            "sentiment_confidence": row["sentiment_confidence"],
            "quote": row["quote"],
            # 关联评论字段（供筛选 / 展示）
            "appid": c["appid"],
            "game": c["game"],
            "posted_at": c["posted_at"],
            "rating": c["rating"],
            "author": c["author"],
            "content": c["content"],
            "overall_sentiment": c["sentiment"],
            "topic": c["topic"],
            "sub_topics": c["sub_topics"],
            "playtime_at_review": c["playtime_at_review"],
            "playtime_forever": c["playtime_forever"],
            "likes": c["likes"],
            "replies": c["replies"],
            "refunded": c["refunded"],
            "early_access": c["early_access"],
            "steam_deck": c["steam_deck"],
            "received_for_free": c["received_for_free"],
            "weighted_vote_score": c["weighted_vote_score"],
            "language": c["language"],
            "source_id": c["source_id"],
        }
        opinions.append(op)
        c["opinions"].append(
            {"path": op["path"], "sentiment": op["sentiment"], "quote": op["quote"]}
        )

    # ---- 标签树计数（仅 Steam） ----
    l1_counts: dict[str, int] = {}
    l2_counts: dict[str, int] = {}
    l3_counts: dict[str, int] = {}
    for v in voices:
        if v["topic"]:
            l1_counts[v["topic"]] = l1_counts.get(v["topic"], 0) + 1
        for l2 in v["sub_topics"]:
            l2_counts[l2] = l2_counts.get(l2, 0) + 1
    for op in opinions:
        parts = op["path"].split("/")
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

    payload = {
        "games": sorted(games.values(), key=lambda g: g["count"], reverse=True),
        "tags": tag_tree,
        "voices": voices,
        "opinions": opinions,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"voices={len(voices)} opinions={len(opinions)} games={len(games)}")
    print(f"-> {OUT_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    sys.exit(main())
