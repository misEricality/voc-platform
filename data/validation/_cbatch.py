# -*- coding: utf-8 -*-
import sys, json, openpyxl
p = r"D:/projects/voc_platform/data/exports/重打500_20260817_125739.xlsx"
wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
ws = wb["comments"]
rows = list(ws.iter_rows(min_row=2, values_only=True))
wb.close()
# 缓存到 json 供后续写回
out = []
for i, r in enumerate(rows):
    out.append({
        "idx": i, "excel_row": i+2, "comment_id": r[0],
        "sentiment": (r[5] or "").strip(),
        "topic": (r[8] or "").strip(),
        "content": (r[11] or "").strip(),
    })
json.dump(out, open(r"D:/projects/voc_platform/data/validation/_comments.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
b = int(sys.argv[1]) if len(sys.argv) > 1 else 0
n = 50
for o in out[b*n:(b+1)*n]:
    c = o["content"].replace("\n", " ")
    print(f"[{o['idx']}] {o['sentiment'][:3]} | {o['topic']} | {c[:130]}")
