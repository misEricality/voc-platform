"""一次性脚本：验证 7 款游戏 × 8/1-8/3 采集完整性"""
import json
import sqlite3

con = sqlite3.connect("D:/projects/voc_platform/data/voc.db")

GAMES = [
    ("steam:730", "Counter-Strike 2", "CS2"),
    ("steam:2358720", "黑神话：悟空", "Black Myth"),
    ("steam:292030", "巫师 3：狂猎", "The Witcher 3"),
    ("steam:289070", "文明 6", "Civilization VI"),
    ("steam:1222140", "底特律：化身为人", "Detroit: Become Human"),
    ("steam:1903340", "光与影：33号远征队", "Expedition 33"),
    ("steam:753640", "星际拓荒", "Outer Wilds"),
]

print(f"{'target_id':<20} {'中文名':<14} {'总数':>6} {'非中文':>6} {'日期范围':<26} {'首末日期'}")
print("=" * 95)

total_all = 0
total_chinese = 0
for target_id, zh_name, en_name in GAMES:
    total = con.execute(
        "SELECT count(*) FROM comments WHERE target_id=?", (target_id,)
    ).fetchone()[0]
    non_zh = con.execute(
        "SELECT count(*) FROM comments WHERE target_id=? AND language != 'schinese'",
        (target_id,),
    ).fetchone()[0]
    r = con.execute(
        "SELECT MIN(date(posted_at)), MAX(date(posted_at)) FROM comments WHERE target_id=?",
        (target_id,),
    ).fetchone()
    date_range = f"{r[0]} ~ {r[1]}" if r[0] else "(空)"
    print(f"{target_id:<20} {zh_name:<14} {total:>6} {non_zh:>6} {date_range:<26}")
    total_all += total
    total_chinese += total - non_zh

print("=" * 95)
print(f"总评论数: {total_all}")
print(f"中文评论数: {total_chinese}")
print(f"非中文数: {total_all - total_chinese}")

print("\n=== 各游戏 8/1-8/3 评论数（应该是 100% 覆盖）===")
for target_id, zh_name, _ in GAMES:
    r = con.execute(
        "SELECT count(*) FROM comments WHERE target_id=? AND posted_at >= '2026-08-01' AND posted_at < '2026-08-04'",
        (target_id,),
    ).fetchone()[0]
    print(f"  {zh_name:<14}: {r} 条")

print("\n=== 各游戏评分（rating）分布 ===")
for target_id, zh_name, _ in GAMES:
    rows = con.execute(
        "SELECT rating, count(*) FROM comments WHERE target_id=? GROUP BY rating",
        (target_id,),
    ).fetchall()
    parts = ", ".join(f"{r}={'正' if r==1 else '负' if r==0 else 'N'}={c}" for r, c in rows)
    print(f"  {zh_name:<14}: {parts}")

print("\n=== 7 款游戏首次入库时间分布 ===")
rows = con.execute(
    "SELECT date(fetched_at) as d, count(*) FROM comments GROUP BY date(fetched_at) ORDER BY d"
).fetchall()
for d, c in rows:
    print(f"  {d}: {c} 条")