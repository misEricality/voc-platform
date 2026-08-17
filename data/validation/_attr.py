# -*- coding: utf-8 -*-
import sys, json, openpyxl
sys.path.insert(0, r"D:/projects/voc_platform")
from src.analyzers import normalize as N

replay = json.load(open(r"D:/projects/voc_platform/data/validation/_replay_opinions.json", encoding="utf-8"))
OLD_L1 = {"其他", "玩法与内容"}

for o in replay:
    l1 = o["actual_path"].split("/")[0] if o["actual_path"] else ""
    if l1 in OLD_L1:
        o["reason_type"] = "旧标签"
        o["reason_detail"] = f"L1={l1}"
    elif o["consistent"]:
        l3 = o["replay_l3"]
        if l3 in N.FALLBACK_L3:
            o["reason_type"] = "兜底规则"
            o["reason_detail"] = f"命中兜底[{l3}]"
        else:
            # 命中关键词（取当前路径对应 L3 命中的词，否则取得分最高的）
            actual_l3 = o["actual_path"].split("/")[-1] if o["actual_path"] else ""
            hits = o["matched"].get(actual_l3, [])
            if not hits:
                # 找最长的命中词
                all_hits = [(k, h) for k, h in o["matched"].items() for h in [h]]
                hits = sorted([h for k,hs in o["matched"].items() for h in hs], key=len, reverse=True)[:3]
            o["reason_type"] = "关键词命中"
            o["reason_detail"] = "命中:" + "/".join(hits[:3])
    else:
        o["reason_type"] = "LLM/其他"
        o["reason_detail"] = f"重放={o['replay_path']}"

json.dump(replay, open(r"D:/projects/voc_platform/data/validation/_replay_opinions.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# 打印批次
b = int(sys.argv[1]) if len(sys.argv) > 1 else 0
n = 50
for o in replay[b*n:(b+1)*n]:
    print(f"[{o['idx']}] {o['sentiment'][:3]} | {o['reason_type']} | {o['actual_path']} | {o['phrase']}")
