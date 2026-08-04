"""验证 Steam filter='recent' 是否严格按 posted_at 倒序"""
import sqlite3

con = sqlite3.connect("D:/projects/voc_platform/data/voc.db")

# 同一游戏（比如黑神话悟空），按 fetched_at 倒序（即采集顺序），看 posted_at 是否也倒序
print("=== 黑神话悟空：采集顺序 vs 时间顺序 是否一致 ===")
print("格式：source_id | fetched_at | posted_at | posted_at ↔ 上一个 posted_at (差值)")
print()

# 取黑神话悟空的前 30 条（采集顺序）
rows = con.execute(
    """
    SELECT source_id, fetched_at, posted_at
    FROM comments
    WHERE target_id = 'steam:2358720'
    ORDER BY id ASC
    LIMIT 30
    """
).fetchall()

prev_ts = None
violations = 0
for i, (src, fts, pts_str) in enumerate(rows):
    from datetime import datetime
    pts = datetime.fromisoformat(pts_str)
    delta_str = ""
    if prev_ts is not None:
        delta = (pts - prev_ts).total_seconds()
        # 时间倒序应该 delta < 0；如果 delta > 0 就是倒序违规
        if delta > 0:
            delta_str = f"   ⚠️ 倒序违规 +{delta:.0f}s"
            violations += 1
        elif delta == 0:
            delta_str = "   (相同时间戳)"
        else:
            delta_str = f"   ({delta:.0f}s)"
    print(f"{i+1:3d}. {src:10} | fetched={fts} | posted={pts}{delta_str}")
    prev_ts = pts

print(f"\n共 {len(rows)} 条，倒序违规 {violations} 次")

print("\n\n=== 跨页翻页时序回跳检测（黑神话）===")
print("说明：filter='recent' 应该 posted_at 严格递减")
print()

# 跨页：用 posted_at 的"局部递增"判断翻页过程中是否有时序回跳
# 简单做法：连续 3 条 posted_at 不严格递减的频次
cur = con.execute(
    """
    SELECT posted_at
    FROM comments
    WHERE target_id = 'steam:2358720'
    ORDER BY id ASC
    """
)
rows_str = [r[0] for r in cur.fetchall()]
from datetime import datetime
rows = [datetime.fromisoformat(s) for s in rows_str]
violations_3 = 0
i = 0
while i < len(rows) - 2:
    # 三连递增说明翻页异常
    if rows[i] < rows[i+1] and rows[i+1] < rows[i+2]:
        violations_3 += 1
        print(f"  三连递增 at id{i}: {rows[i]} < {rows[i+1]} < {rows[i+2]}")
        i += 1
    else:
        i += 1

print(f"\n跨页时序回跳数: {violations_3} 次（共 {len(rows)} 条黑神话悟空评论）")
print(f"占比: {violations_3 / len(rows) * 100:.1f}%")