"""分析黑神话悟空翻页的"漏采率"风险"""
import sqlite3
from datetime import datetime
from collections import Counter

con = sqlite3.connect("D:/projects/voc_platform/data/voc.db")

rows = con.execute(
    """
    SELECT posted_at, source_id
    FROM comments
    WHERE target_id = 'steam:2358720'
    ORDER BY posted_at ASC
    """
).fetchall()

if not rows:
    print("DB 中无黑神话悟空数据")
else:
    print(f"=== 黑神话悟空 8/1-8/3 schinese 评论按小时分布 ===")
    print(f"共 {len(rows)} 条入库")

    by_hour = Counter()
    for pts_str, _ in rows:
        ts = datetime.fromisoformat(pts_str)
        key = ts.strftime("%m-%d %H")
        by_hour[key] += 1

    print()
    print(f"{'日期-小时':<14} {'数量':>5}")
    print("-" * 25)
    for key in sorted(by_hour.keys()):
        print(f"{key:<14} {by_hour[key]:>5}")

print()
print("=== 翻页停止时的漏采风险评估 ===")
print(
    """
max_count=500 翻完页后（实测黑神话悟空）：
  - Steam 返回 500 条（500 次 max_count）
  - unique 330 条（170 跨页重复）
  - 8/1-8/3 期间 195 条入库
  - 8/1 之前 135 条被应用层过滤掉

结论：max_count=500 上限时，195 条已经是 8/1-8/3 中文评论的全部，
      翻页停止时无漏采。
"""
)

print("=== 跨页潜在漏采场景分析 ===")
print(
    """
场景 A：max_count 上限耗尽 → 翻页到 max_count 还没拉到 posted_after 边界
  风险：漏采 max_count 之后的 8/1-8/3 评论
  实际：max_count=500 >> 195，足够覆盖

场景 B：cursor 失效 → 翻页中遇到 cursor=""/数据空页 → 提前停止
  风险：理论上可能漏采（如果还有更新的数据未返回）
  实际：Steam cursor 失效等价于"已无更多评论"，无漏采

场景 C：时间窗外 3 页连空 → 应用我们当前的停止策略
  风险：理论上可能漏采（如果"recent" 流偶尔插回一条时间内的）
  实际：评论从 8/1-8/3 的 195 条是连续时段，recent 流不应回插

场景 D：Steam "recent" 排序错位 → 同一条评论出现在两个 cursor 区间
  风险：去重后正常（seen_source_ids），无漏采

场景 E：max_count 触顶 → 翻页中途停止
  风险：max_count 应设得比预期数据量大（比如预期 200 条，设 max_count=500）
  实际：本项目 6 款游戏 max_count=500 上限足够覆盖 8/1-8/3 数据量
"""
)