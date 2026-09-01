"""从当前 DB 重新生成原型内嵌数据（方案4：opinions 接入）并替换 voc-platform-prototype.html

背景（2026-08-14）：原型内嵌的 DATA 是 8/7 旧 v2 数据（opinions 全空），
导致 L3 标签筛选 / 标签情感模式查不出数据。本脚本从 DB（方案4 全量重打后）
重新生成 games / tags / voices，替换 HTML 里的 `const DATA = {...}`。

用法：
    python scripts/dev/gen_prototype_data.py
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys_path = str(PROJECT_ROOT)
import sys
sys.path.insert(0, sys_path)

from src.analyzers.normalize import build_l3_mapping, load_hierarchy

HTML_PATH = PROJECT_ROOT / "product" / "prototype" / "voc-platform-prototype.html"
DB_PATH = PROJECT_ROOT / "data" / "voc.db"

# full_path → (L1, L2, L3)，用 mapping 精确解析（避免"整活/梗"这类含斜杠 L3 被误拆）
_PATH2PARTS = {
    (f"{l1}/{l3}" if "/" in l3 else f"{l1}/{l2}/{l3}"): (l1, l2, l3)
    for l3, (l1, l2) in build_l3_mapping(load_hierarchy()).items()
}


def parse_path(path: str) -> tuple[str, str, str] | None:
    """full_path → (L1, L2, L3)；无法解析则回退 split"""
    if path in _PATH2PARTS:
        return _PATH2PARTS[path]
    parts = path.split("/")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], parts[1]
    return None


def load_voices(conn: sqlite3.Connection) -> list[dict]:
    """从 comments + comment_opinions 生成 voices（方案4 格式）"""
    conn.row_factory = sqlite3.Row
    comments = conn.execute(
        "SELECT * FROM comments WHERE platform='steam' ORDER BY id"
    ).fetchall()
    # opinions 按 comment_id 分组
    opinions_by_comment: dict[int, list[dict]] = defaultdict(list)
    for op in conn.execute(
        "SELECT * FROM comment_opinions ORDER BY id"
    ).fetchall():
        opinions_by_comment[op["comment_id"]].append(op)

    voices = []
    for c in comments:
        extra = {}
        if c["extra_json"]:
            try:
                extra = json.loads(c["extra_json"]) or {}
            except Exception:
                extra = {}
        # 字段名映射（DB/extra_json → 界面字段）
        target_id = c["target_id"] or ""
        appid = target_id.split(":")[-1] if ":" in target_id else target_id
        game = ""
        if c["extra_meta"]:
            try:
                game = (json.loads(c["extra_meta"]) or {}).get("name", "") or ""
            except Exception:
                game = ""
        ops = [
            {
                "path": op["full_path"],
                "sentiment": op["sentiment"],
                "quote": op["quote"],
            }
            for op in opinions_by_comment.get(c["id"], [])
            if op["full_path"]
        ]
        # sub_topics：从 opinions 提取 L2 名（界面 L2 筛选依赖此字段，方案4 下需回填）
        l2_set = set()
        for op in ops:
            parsed = parse_path(op["path"])
            if parsed:
                l2_set.add(parsed[1])
        voices.append({
            "id": c["id"],
            "appid": appid,
            "game": game,
            "content": c["content"],
            "author": c["author_id"],
            "rating": c["rating"],
            "language": c["language"],
            "posted_at": c["posted_at"],
            "sentiment": c["sentiment"],
            "sentiment_score": c["sentiment_score"],
            "sentiment_confidence": c["sentiment_confidence"],
            "topic": c["topic"],
            "sub_topics": sorted(l2_set),
            "likes": c["likes"],
            "replies": c["replies"],
            "playtime_at_review": extra.get("playtime_at_review"),
            "playtime_forever": extra.get("playtime_forever"),
            "refunded": extra.get("refunded", False),
            "early_access": extra.get("written_during_early_access", False),
            "steam_deck": extra.get("primarily_steam_deck", False),
            "received_for_free": extra.get("received_for_free", False),
            "weighted_vote_score": extra.get("weighted_vote_score", 0.5),
            "source_id": c["source_id"],
            "opinions": ops,
        })
    return voices


def build_tags(voices: list[dict]) -> list[dict]:
    """从 voices 的 opinions full_path 聚合三级标签树（L1→L2→L3，count 逐级累加）"""
    l3_counter: dict[tuple[str, str, str], int] = defaultdict(int)
    for v in voices:
        for op in v["opinions"]:
            parsed = parse_path(op["path"])
            if parsed:
                l3_counter[parsed] += 1

    # 构建树
    tree: dict[str, dict] = {}  # l1 -> {"l2": {"l3": count}}
    for (l1, l2, l3), cnt in l3_counter.items():
        tree.setdefault(l1, {}).setdefault(l2, {})[l3] = cnt

    tags = []
    for l1, l2map in tree.items():
        l2_nodes = []
        l1_count = 0
        for l2, l3map in l2map.items():
            l3_nodes = [{"name": l3, "count": cnt} for l3, cnt in l3map.items()]
            l2_count = sum(n["count"] for n in l3_nodes)
            l1_count += l2_count
            l2_nodes.append({"name": l2, "count": l2_count, "children": l3_nodes})
        l2_nodes.sort(key=lambda n: -n["count"])
        tags.append({"name": l1, "count": l1_count, "children": l2_nodes})
    tags.sort(key=lambda n: -n["count"])
    return tags


def build_games(voices: list[dict]) -> list[dict]:
    counter: dict[str, int] = defaultdict(int)
    name_map: dict[str, str] = {}
    for v in voices:
        counter[v["appid"]] += 1
        name_map[v["appid"]] = v["game"]
    games = [
        {"appid": appid, "name": name_map.get(appid, ""), "count": cnt}
        for appid, cnt in counter.items()
    ]
    games.sort(key=lambda g: -g["count"])
    return games


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    voices = load_voices(conn)
    tags = build_tags(voices)
    games = build_games(voices)
    conn.close()

    data = {"games": games, "tags": tags, "voices": voices}
    json_str = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    html = HTML_PATH.read_text(encoding="utf-8")
    # 定位 const DATA = {...}; 替换中间内容（json_str 是 json.dumps 产物，必然合法）
    start_marker = "const DATA = "
    end_marker = ";\nconst VOICES"
    start = html.index(start_marker) + len(start_marker)
    end = html.index(end_marker, start)
    new_html = html[:start] + json_str + html[end:]

    HTML_PATH.write_text(new_html, encoding="utf-8")
    print(f"✅ 已生成并替换 DATA")
    print(f"  games: {len(games)} 款 | tags L1: {len(tags)} 个 | voices: {len(voices)} 条")
    print(f"  观点总数: {sum(len(v['opinions']) for v in voices)}")
    print(f"  文件: {HTML_PATH}")
    print(f"  文件大小: {HTML_PATH.stat().st_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
