"""一次性脚本：展示回采后的真实数据样本"""
import json
import sqlite3
from pathlib import Path

con = sqlite3.connect("D:/projects/voc_platform/data/voc.db")
cur = con.cursor()

print("=" * 70)
print("前 5 条评论：likes/replies/新字段实际值")
print("=" * 70)
cur.execute(
    "SELECT source_id, likes, replies, extra_json, content FROM comments LIMIT 5"
)
for src, likes, replies, ej, content in cur.fetchall():
    e = json.loads(ej)
    print(f"\nsource_id: {src}")
    print(f"  评论: {content[:60]}")
    print(f"  likes={likes}  replies={replies}")
    print(
        f"  weighted_vote_score={e.get('weighted_vote_score')!r}"
        f"  refunded={e.get('refunded')}"
    )
    print(
        f"  has developer_response field: {'developer_response' in e} "
        f"(value type: {type(e.get('developer_response')).__name__})"
    )

print("\n" + "=" * 70)
print("likes / replies 分布")
print("=" * 70)
mn, mx, av = cur.execute(
    "SELECT MIN(likes), MAX(likes), AVG(likes) FROM comments WHERE likes IS NOT NULL"
).fetchone()
print(f"likes   min={mn}  max={mx}  avg={av:.2f}")
mn, mx, av = cur.execute(
    "SELECT MIN(replies), MAX(replies), AVG(replies) FROM comments WHERE replies IS NOT NULL"
).fetchone()
print(f"replies min={mn}  max={mx}  avg={av:.2f}")

print("\n" + "=" * 70)
print("weighted_vote_score TOP 5")
print("=" * 70)
rows = cur.execute(
    """
    SELECT json_extract(extra_json, '$.weighted_vote_score'), COUNT(*)
    FROM comments
    WHERE extra_json LIKE '%weighted_vote_score%'
    GROUP BY 1
    ORDER BY 1 DESC
    LIMIT 5
    """
).fetchall()
for v, c in rows:
    print(f"  {v}: {c} 条")

print("\n" + "=" * 70)
print("developer_response 实际为非空的样本（CS2 中文评论里有开发者回复的）")
print("=" * 70)
rows = cur.execute(
    """
    SELECT json_extract(extra_json, '$.developer_response'),
           json_extract(extra_json, '$.timestamp_dev_responded'),
           content
    FROM comments
    WHERE json_extract(extra_json, '$.developer_response') IS NOT NULL
      AND json_extract(extra_json, '$.developer_response') != ''
    LIMIT 5
    """
).fetchall()
if not rows:
    print("  (当前 50 条中文评论里没有开发者回复的——正常，CS2 中文社区少见)")
else:
    for resp, ts, content in rows:
        print(f"  评论: {content[:50]}")
        print(f"    回复: {(resp or '')[:60]!r}")
        print(f"    时间戳: {ts}")

print("\n" + "=" * 70)
print("likes_refreshed_at 时间分布")
print("=" * 70)
cur.execute(
    "SELECT COUNT(*), MIN(likes_refreshed_at), MAX(likes_refreshed_at) FROM comments WHERE likes_refreshed_at IS NOT NULL"
)
total, mn, mx = cur.fetchone()
print(f"  共 {total} 条已回采")
print(f"  最早: {mn}")
print(f"  最晚: {mx}")