# -*- coding: utf-8 -*-
import json, sys, os
path = r"D:/projects/voc_platform/data/validation/_cmt_results.json"
res = {}
if os.path.exists(path):
    res = json.load(open(path, encoding="utf-8"))
for item in sys.argv[1].split(";"):
    item = item.strip()
    if not item: continue
    idx, rest = item.split(":", 1)
    parts = rest.split(",")
    tag = int(parts[0]); senti = int(parts[1])
    cl1 = parts[2] if len(parts) > 2 and parts[2] != "-" else None
    csenti = parts[3] if len(parts) > 3 and parts[3] != "-" else None
    res[idx] = {"tag": tag, "senti": senti, "correct_topic": cl1, "correct_senti": csenti}
json.dump(res, open(path, "w", encoding="utf-8"), ensure_ascii=False)
print("stored:", len(res))
