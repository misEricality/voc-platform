"""临时脚本：调试 UNIQUE 冲突
复现 fetch_comments 拉 2358720 看 source_id 是否有重复
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from datetime import datetime

from src.collectors.steam import SteamCollector

collector = SteamCollector()
raws = collector.collect(
    "2358720",
    max_count=500,
    language="schinese",
    posted_after=datetime(2026, 8, 1, 0, 0, 0),
    posted_before=datetime(2026, 8, 4, 0, 0, 0),
)
print(f"拉取 {len(raws)} 条")

ids = [r.source_id for r in raws]
unique_ids = set(ids)
print(f"unique source_id 数: {len(unique_ids)}")
print(f"有重复: {len(ids) != len(unique_ids)}")

# 找重复
from collections import Counter
counter = Counter(ids)
dups = [(k, v) for k, v in counter.items() if v > 1]
print(f"重复 source_id: {dups[:5]}")