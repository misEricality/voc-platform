"""一次性脚本：检查 8/3 当天采集的 CS2 中文评论

> ⚠️ 2026-08-23 注：CS2 数据已从主库归档到 `data/archive/online_games_2026-08-23.db`。
> 本脚本保留作为历史记录，运行时会指向主库发现 0 条。
"""
import sqlite3

con = sqlite3.connect("D:/projects/voc_platform/data/voc.db")
print("总评论数:", con.execute("SELECT count(*) FROM comments").fetchone()[0])

print("\n=== 8/3 当天采集（posted_at = 2026-08-03）===")
r = con.execute(
    """
    SELECT count(*), MIN(posted_at), MAX(posted_at)
    FROM comments
    WHERE posted_at >= '2026-08-03' AND posted_at < '2026-08-04'
    """
).fetchone()
print(f"8/3 评论数: {r[0]}")
print(f"最早: {r[1]}")
print(f"最晚: {r[2]}")

print("\n=== 全部评论按 posted_at 日期分布 ===")
r = con.execute(
    """
    SELECT date(posted_at) as d, count(*)
    FROM comments
    GROUP BY date(posted_at)
    ORDER BY d
    """
).fetchall()
for d, c in r:
    print(f"  {d}: {c} 条")

print("\n=== 8/3 评论按 rating 分布 ===")
r = con.execute(
    """
    SELECT rating, count(*)
    FROM comments
    WHERE posted_at >= '2026-08-03' AND posted_at < '2026-08-04'
    GROUP BY rating
    """
).fetchall()
labels = {1: "推荐", 0: "不推荐", None: "未知"}
for rating, c in r:
    print(f"  rating={rating} ({labels.get(rating, rating)}): {c}")

print("\n=== 8/3 前 3 条评论 ===")
r = con.execute(
    """
    SELECT source_id, posted_at, language, rating, content
    FROM comments
    WHERE posted_at >= '2026-08-03' AND posted_at < '2026-08-04'
    ORDER BY posted_at ASC
    LIMIT 3
    """
).fetchall()
for src, ts, lang, rating, content in r:
    print(f"  {src} {ts} | lang={lang} | rating={rating}")
    print(f"    内容: {content[:80]}")

print("\n=== 8/3 评论按语言分布（确保只 schinese）===")
r = con.execute(
    """
    SELECT language, count(*)
    FROM comments
    WHERE posted_at >= '2026-08-03' AND posted_at < '2026-08-04'
    GROUP BY language
    """
).fetchall()
for lang, c in r:
    print(f"  {lang}: {c}")

print("\n=== 8/3 评论的 likes/replies 状态（首次采集，应为 None）===")
r = con.execute(
    """
    SELECT count(*) FILTER (WHERE likes IS NULL) as null_likes,
           count(*) FILTER (WHERE likes IS NOT NULL) as has_likes,
           count(*) FILTER (WHERE replies IS NULL) as null_replies,
           count(*) FILTER (WHERE likes_refreshed_at IS NULL) as null_refresh
    FROM comments
    WHERE posted_at >= '2026-08-03' AND posted_at < '2026-08-04'
    """
).fetchone()
print(f"  likes IS NULL: {r[0]} / likes IS NOT NULL: {r[1]}")
print(f"  replies IS NULL: {r[2]}")
print(f"  likes_refreshed_at IS NULL: {r[3]}（应该都是 NULL）")