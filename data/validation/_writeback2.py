# -*- coding: utf-8 -*-
import sys, json, openpyxl
sys.path.insert(0, r"D:/projects/voc_platform")
from src.analyzers import normalize as N

p = r"D:/projects/voc_platform/data/exports/重打500_20260817_125739.xlsx"
op = json.load(open(r"D:/projects/voc_platform/data/validation/_op_results.json", encoding="utf-8"))
cmt = json.load(open(r"D:/projects/voc_platform/data/validation/_cmt_results.json", encoding="utf-8"))
replay = json.load(open(r"D:/projects/voc_platform/data/validation/_replay_opinions.json", encoding="utf-8"))

hierarchy = N.load_hierarchy("gaming")
l3_map = N.build_l3_mapping(hierarchy)

def l3_to_path(l3):
    if not l3: return None
    hit = l3_map.get(l3)
    if not hit: return l3
    l1, l2 = hit
    return f"{l1}/{l2}/{l3}"

# 归因文本
def attr_text(r):
    t = r["reason_type"]
    d = r["reason_detail"]
    if t == "关键词命中":
        return f"关键词命中: {d}"
    if t == "兜底规则":
        return f"兜底规则: {d}"
    if t == "旧标签":
        return f"错用旧标签: {d}"
    return f"LLM/其他: {d}"

try:
    wb = openpyxl.load_workbook(p)
except PermissionError:
    print("ERROR: 文件被占用，请关闭后重试")
    raise SystemExit(1)

# ---- opinions ----
ws = wb["opinions"]
ws.cell(row=1, column=13, value="标注验证")
ws.cell(row=1, column=14, value="情感验证")
ws.cell(row=1, column=15, value="标注归因")
ws.cell(row=1, column=16, value="正确标签")
for i, r in enumerate(replay):
    row = r["excel_row"]
    v = op[str(i)]
    ws.cell(row=row, column=13, value=v["tag"])
    ws.cell(row=row, column=14, value=v["senti"])
    ws.cell(row=row, column=15, value=attr_text(r))
    if v["tag"] == 0 and v["correct_l3"]:
        ws.cell(row=row, column=16, value=l3_to_path(v["correct_l3"]))
    elif v["correct_senti"]:
        ws.cell(row=row, column=16, value=f"情感应为 {v['correct_senti']}")
print("opinions 写回完成")

# ---- comments ----
ws2 = wb["comments"]
ws2.cell(row=1, column=13, value="主题验证")
ws2.cell(row=1, column=14, value="情感验证")
ws2.cell(row=1, column=15, value="主题归因")
ws2.cell(row=1, column=16, value="正确标签")
cmt_data = json.load(open(r"D:/projects/voc_platform/data/validation/_comments.json", encoding="utf-8"))
for c in cmt_data:
    row = c["excel_row"]
    v = cmt[str(c["idx"])]
    ws2.cell(row=row, column=13, value=v["tag"])
    ws2.cell(row=row, column=14, value=v["senti"])
    # 归因：空值 → 缺失；旧标签 → 错用旧标签；否则 → 主题概括不准确
    if not c["topic"]:
        ws2.cell(row=row, column=15, value="数据缺失(空值)")
    elif c["topic"] in ("其他", "玩法与内容"):
        ws2.cell(row=row, column=15, value=f"错用旧标签: L1={c['topic']}")
    else:
        ws2.cell(row=row, column=15, value="L1主题概括")
    if v["tag"] == 0 and v["correct_topic"]:
        ws2.cell(row=row, column=16, value=v["correct_topic"])
    elif v["correct_senti"]:
        ws2.cell(row=row, column=16, value=f"情感应为 {v['correct_senti']}")
print("comments 写回完成")

wb.save(p)
print("saved:", p)
