# -*- coding: utf-8 -*-
import json
from collections import Counter
op = json.load(open(r"D:/projects/voc_platform/data/validation/_op_results.json", encoding="utf-8"))
cmt = json.load(open(r"D:/projects/voc_platform/data/validation/_cmt_results.json", encoding="utf-8"))

n_op = len(op); n_cmt = len(cmt)
print(f"opinions: {n_op}   comments: {n_cmt}")

# opinions 标注（严格L3）与情感
op_tag = Counter(v["tag"] for v in op.values())
op_senti = Counter(v["senti"] for v in op.values())
print(f"\n[opinions] 标注验证(严格L3): 正确={op_tag[1]} 错误={op_tag[0]} 准确率={op_tag[1]/n_op:.2%}")
print(f"[opinions] 情感验证: 正确={op_senti[1]} 错误={op_senti[0]} 准确率={op_senti[1]/n_op:.2%}")

# comments 主题与情感
cmt_tag = Counter(v["tag"] for v in cmt.values())
cmt_senti = Counter(v["senti"] for v in cmt.values())
print(f"\n[comments] 主题验证(L1): 正确={cmt_tag[1]} 错误={cmt_tag[0]} 准确率={cmt_tag[1]/n_cmt:.2%}")
print(f"[comments] 情感验证: 正确={cmt_senti[1]} 错误={cmt_senti[0]} 准确率={cmt_senti[1]/n_cmt:.2%}")

# 归因分布（opinions）
replay = json.load(open(r"D:/projects/voc_platform/data/validation/_replay_opinions.json", encoding="utf-8"))
attr = Counter(r["reason_type"] for r in replay)
print(f"\n[opinions] 归因分布: {dict(attr)}")

# 标注错误的归因分布
tag_err_idx = [int(k) for k,v in op.items() if v["tag"]==0]
err_attr = Counter(replay[i]["reason_type"] for i in tag_err_idx)
print(f"[opinions] 标注错误(n={len(tag_err_idx)}) 的归因分布: {dict(err_attr)}")

# 兜底类错误 vs 命中错误
fb_err = sum(1 for i in tag_err_idx if replay[i]["reason_type"]=="兜底规则")
kw_err = sum(1 for i in tag_err_idx if replay[i]["reason_type"]=="关键词命中")
old_err = sum(1 for i in tag_err_idx if replay[i]["reason_type"]=="旧标签")
print(f"  其中: 兜底规则={fb_err}, 关键词命中误判={kw_err}, 旧标签={old_err}")

# 正确标签的 L1 分布（错误条目）
from collections import Counter as C
correct_l1 = C(v["correct_l3"].split("·")[0] if v.get("correct_l3") else None for v in op.values() if v["tag"]==0)
# 映射 L3 到 L1（用 replay 里的 full_path 无法，简单统计 correct_l3 原始值）
print(f"\n[opinions] 标注错误 → 正确标签 L3 分布:")
for l3, c in C(v["correct_l3"] for v in op.values() if v["tag"]==0 and v["correct_l3"]).most_common(15):
    print(f"  {c:3d}  {l3}")

# comments 正确标签分布
print(f"\n[comments] 主题错误 → 正确 L1 分布:")
for l1, c in C(v["correct_topic"] for v in cmt.values() if v["tag"]==0 and v["correct_topic"]).most_common(10):
    print(f"  {c:3d}  {l1}")
