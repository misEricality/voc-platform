"""查看评论观点（opinion）工具（2026-08-05 一次性 + 长期保留）

用法示例：
    python scripts/dev/dump_opinions.py --comment-id 51
    python scripts/dev/dump_opinions.py --target steam:730 --limit 3
    python scripts/dev/dump_opinions.py --label 玩法机制 --limit 10
    python scripts/dev/dump_opinions.py --sentiment negative --limit 5
    python scripts/dev/dump_opinions.py --stat           # 仅看统计
    python scripts/dev/dump_opinions.py --outlier        # 仅看越界评论

输出格式：
- 单条：原声 + 情感 + topic/sub_topics + 每个 opinion（label/level/quote/quote span）
- 多条：表格 + 摘要
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()

import yaml

DEFAULT_DB = "data/voc.db"


def load_valid_labels() -> tuple[set[str], set[str]]:
    """加载合法 L1/L2 标签集合"""
    cfg = yaml.safe_load(
        Path("config/topics/gaming.yaml").read_text(encoding="utf-8")
    )
    all_l1 = set(cfg.get("primary", []))
    all_l2 = set()
    for subs in cfg.get("hierarchy", {}).values():
        if isinstance(subs, dict):
            all_l2.update(subs.keys())
    return all_l1, all_l2


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_comments(
    conn: sqlite3.Connection,
    comment_id: int | None = None,
    target_id: str | None = None,
    sentiment: str | None = None,
    label: str | None = None,
    limit: int = 10,
) -> list[sqlite3.Row]:
    """按条件查评论（带 analyzed_at IS NOT NULL）"""
    sql = """
        SELECT c.id, c.platform, c.target_id, c.source_id, c.content,
               c.author, c.posted_at, c.language, c.sentiment,
               c.sentiment_score, c.sentiment_confidence,
               c.topic, c.sub_topics, c.analyzed_at,
               json_extract(c.extra_meta, '$.name') as target_name
        FROM comments c
        WHERE c.analyzed_at IS NOT NULL
    """
    params: list = []
    if comment_id is not None:
        sql += " AND c.id = ?"
        params.append(comment_id)
    if target_id:
        sql += " AND c.target_id = ?"
        params.append(target_id)
    if sentiment:
        sql += " AND c.sentiment = ?"
        params.append(sentiment)
    if label:
        # 匹配 topic 或 sub_topics（JSON LIKE）
        sql += " AND (c.topic = ? OR c.sub_topics LIKE ?)"
        params.append(label)
        params.append(f'%"{label}"%')
    sql += " ORDER BY c.analyzed_at DESC LIMIT ?"
    params.append(limit)
    return list(conn.execute(sql, params))


def fetch_opinions(
    conn: sqlite3.Connection, comment_ids: list[int]
) -> dict[int, list[sqlite3.Row]]:
    """批量查 opinion（按 comment_id 分组）"""
    if not comment_ids:
        return {}
    placeholders = ",".join("?" * len(comment_ids))
    rows = list(conn.execute(
        f"SELECT * FROM comment_opinions WHERE comment_id IN ({placeholders}) "
        f"ORDER BY comment_id, id",
        comment_ids,
    ))
    grouped: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        grouped.setdefault(r["comment_id"], []).append(r)
    return grouped


def print_one_comment(
    comment: sqlite3.Row,
    opinions: list[sqlite3.Row],
    content_max_len: int = 400,
) -> None:
    """打印单条评论详情"""
    print("=" * 78)
    print(f"[Comment id={comment['id']}] {comment['target_id']} ({comment['target_name'] or '?'})")
    print(f"author: {comment['author'] or '匿名'} | posted: {comment['posted_at']} | lang: {comment['language']}")
    print("-" * 78)
    content = comment["content"] or ""
    if len(content) > content_max_len:
        content = content[:content_max_len] + f"... [+{len(content) - content_max_len}字]"
    print(f"原声（content {len(comment['content'] or '')} 字）:")
    for line in content.split("\n"):
        print(f"  {line}")
    print("-" * 78)
    print(
        f"分析结果:"
        f"\n  sentiment: {comment['sentiment']}"
        f" (score={comment['sentiment_score']:+.2f}, conf={comment['sentiment_confidence']:.2f})"
        f"\n  topic: {comment['topic'] or '(空)'}"
    )
    try:
        subs = json.loads(comment["sub_topics"]) if comment["sub_topics"] else []
    except Exception:
        subs = []
    print(f"  sub_topics: {subs}")
    print("-" * 78)
    if opinions:
        print(f"观点（opinions）: {len(opinions)} 个")
        for op in opinions:
            span = ""
            if op["quote_start"] is not None and op["quote_end"] is not None:
                span = f" (字符位置: {op['quote_start']}-{op['quote_end']})"
            sent = (op["sentiment"] or "?").upper()[0]
            print(f"  [{sent}] {op['full_path']:<30} → \"{op['quote']}\"{span}")
    else:
        print("观点（opinions）: 无（待重跑或 LLM 未返回）")


def print_table(comments: list[sqlite3.Row]) -> None:
    """打印多行摘要"""
    if not comments:
        print("(无结果)")
        return
    print(f"找到 {len(comments)} 条评论")
    print("-" * 78)
    for c in comments:
        try:
            subs = json.loads(c["sub_topics"]) if c["sub_topics"] else []
        except Exception:
            subs = []
        sentiment_short = (c["sentiment"] or "?")[0].upper()
        score = c["sentiment_score"] or 0
        print(
            f"[id={c['id']:>5}] {sentiment_short}{score:+.2f}  "
            f"topic={c['topic'] or '-':<8} subs={','.join(subs[:3])}{'...' if len(subs) > 3 else ''}  "
            f"({c['target_id']})"
        )
        content = c["content"] or ""
        if len(content) > 80:
            content = content[:80] + "..."
        print(f"          原声: {content}")


def print_stats(conn: sqlite3.Connection) -> None:
    """输出整体统计"""
    cur = conn.cursor()
    print("=" * 78)
    print("整体统计")
    print("=" * 78)

    cur.execute("SELECT COUNT(*) FROM comments")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM comments WHERE analyzed_at IS NOT NULL")
    analyzed = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM comment_opinions")
    opinions = cur.fetchone()[0]
    print(f"总评论: {total}")
    print(f"已分析: {analyzed} ({analyzed*100/total:.1f}%)")
    print(f"观点总数: {opinions}")
    print(f"平均每条评论的观点数: {opinions/analyzed:.1f}" if analyzed else "")

    # 越界
    all_l1, all_l2 = load_valid_labels()
    cur.execute("SELECT id, topic, sub_topics FROM comments WHERE analyzed_at IS NOT NULL")
    bad_l1 = Counter()
    bad_l2 = Counter()
    for cid, topic, sub_json in cur.fetchall():
        if topic and topic not in all_l1:
            bad_l1[topic] += 1
        try:
            subs = json.loads(sub_json) if sub_json else []
        except Exception:
            subs = []
        for s in subs:
            if s not in all_l2:
                bad_l2[s] += 1
    print()
    print(f"topic 越界: {sum(bad_l1.values())} 条（{dict(bad_l1)}）")
    print(f"sub_topic 越界: {sum(bad_l2.values())} 处（{dict(bad_l2)}）")

    # 情感分布
    cur.execute(
        "SELECT sentiment, COUNT(*) FROM comments "
        "WHERE analyzed_at IS NOT NULL GROUP BY sentiment"
    )
    print()
    print("情感分布:")
    for s, c in cur.fetchall():
        print(f"  {s:<10} {c:>5}  {c*100/analyzed:>5.1f}%")

    # 观点按 full_path 分布 TOP 10
    cur.execute(
        "SELECT full_path, sentiment, COUNT(*) FROM comment_opinions "
        "GROUP BY full_path, sentiment ORDER BY COUNT(*) DESC LIMIT 10"
    )
    print()
    print("观点 TOP 10 (按路径):")
    for path, sent, c in cur.fetchall():
        print(f"  [{sent}] {path:<30} {c:>5}")


def print_outliers(conn: sqlite3.Connection, limit: int = 20) -> None:
    """打印当前 DB 中所有越界评论"""
    all_l1, all_l2 = load_valid_labels()
    cur = conn.cursor()
    cur.execute("SELECT id, content, topic, sub_topics FROM comments WHERE analyzed_at IS NOT NULL")
    outliers = []
    for cid, content, topic, sub_json in cur.fetchall():
        try:
            subs = json.loads(sub_json) if sub_json else []
        except Exception:
            subs = []
        sub_bad = [s for s in subs if s not in all_l2]
        topic_bad = topic not in all_l1 if topic else False
        if sub_bad or topic_bad:
            outliers.append((cid, topic, sub_bad, content))
    if not outliers:
        print("✅ 当前无越界评论")
        return
    print(f"⚠️ 当前 DB 中有 {len(outliers)} 条越界评论（仅展示前 {min(limit, len(outliers))} 条）:")
    print("-" * 78)
    for cid, topic, sub_bad, content in outliers[:limit]:
        print(f"[id={cid}] topic={topic}")
        print(f"  越界 sub: {sub_bad}")
        print(f"  原声: {(content or '')[:120]}...")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="查看 VoC 评论观点（opinion）")
    parser.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    parser.add_argument("--comment-id", type=int, help="查指定评论 id")
    parser.add_argument("--target", help="查指定 target_id（如 steam:730）")
    parser.add_argument("--sentiment", choices=["positive", "negative", "neutral"], help="按情感过滤")
    parser.add_argument("--label", help="按标签过滤（匹配 topic 或 sub_topic）")
    parser.add_argument("--limit", type=int, default=10, help="返回条数（默认 10）")
    parser.add_argument("--stat", action="store_true", help="仅输出整体统计")
    parser.add_argument("--outlier", action="store_true", help="仅输出越界评论")
    parser.add_argument("--full", action="store_true", help="单条详情模式：完整打印 opinion")
    args = parser.parse_args()

    conn = get_conn(args.db)

    if args.stat:
        print_stats(conn)
        conn.close()
        return

    if args.outlier:
        print_outliers(conn, limit=args.limit)
        conn.close()
        return

    comments = fetch_comments(
        conn,
        comment_id=args.comment_id,
        target_id=args.target,
        sentiment=args.sentiment,
        label=args.label,
        limit=args.limit,
    )

    if not comments:
        print("(无结果)")
        conn.close()
        return

    # 单条详情 vs 多条摘要
    if args.comment_id is not None or args.full:
        comment_ids = [c["id"] for c in comments]
        opinions_map = fetch_opinions(conn, comment_ids)
        for c in comments:
            print_one_comment(c, opinions_map.get(c["id"], []))
    else:
        print_table(comments)
        print()
        print("💡 提示：加 --full 或指定 --comment-id 查详情；用 --outlier 看越界评论")

    conn.close()


if __name__ == "__main__":
    main()