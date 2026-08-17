# -*- coding: utf-8 -*-
import sys, json, openpyxl
sys.path.insert(0, r"D:/projects/voc_platform")
from src.analyzers import normalize as N

p = r"D:/projects/voc_platform/data/exports/重打500_20260817_125739.xlsx"
hierarchy = N.load_hierarchy("gaming")
defs = N.load_definitions("gaming")
kw_idx = N.build_keyword_index(defs)
l3_map = N.build_l3_mapping(hierarchy)

# 读取 opinions
wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
ws = wb["opinions"]
out = []
for i, r in enumerate(ws.iter_rows(min_row=2, values_only=True)):
    phrase = (r[9] or "").strip()
    actual_path = (r[8] or "").strip()
    # 重放 match_l3
    l3 = N.match_l3(phrase, kw_idx, defs)
    replay_path = N.map_l3_to_path(l3, l3_map) if l3 else None
    # 命中关键词归因
    matched = {}
    p2 = phrase
    for term in N.DISAMBIGUATION_SUBSTRINGS:
        p2 = p2.replace(term, "×")
    for l3k, kws in kw_idx.items():
        hits = [k for k in kws if k and k in p2]
        if hits:
            matched[l3k] = hits
    out.append({
        "idx": i,
        "excel_row": i + 2,
        "opinion_id": r[0],
        "comment_id": r[1],
        "phrase": phrase,
        "sentiment": (r[6] or "").strip(),
        "actual_path": actual_path,
        "replay_l3": l3,
        "replay_path": replay_path,
        "matched": matched,
        "consistent": (actual_path == replay_path),
    })
wb.close()
json.dump(out, open(r"D:/projects/voc_platform/data/validation/_replay_opinions.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# 统计
cons = sum(1 for o in out if o["consistent"])
print(f"opinions: {len(out)}")
print(f"重放与实际 full_path 一致: {cons} ({cons/len(out):.1%})")
print(f"重放与实际不一致: {len(out)-cons}")
# 不一致样例
print("\n不一致样例（前20）:")
c = 0
for o in out:
    if not o["consistent"]:
        print(f"  idx{o['idx']} phrase={o['phrase'][:30]!r} actual={o['actual_path']} | replay={o['replay_path']}")
        c += 1
        if c >= 20: break
