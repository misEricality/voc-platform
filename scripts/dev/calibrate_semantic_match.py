"""bge 语义匹配校准脚本（阶段 1 · 诊断用）

目的：评估「观点短语 ↔ L3 定义」的 bge 语义相似度，能否救回
关键词包含匹配兜底失败的短语（黑话 / 专名 / 口语变体，如"挂""康纳"）。

用法（需装有 sentence-transformers + torch 的环境）：
    python scripts/dev/calibrate_semantic_match.py

说明：
  - 只读：不修改数据库、不改动任何标注结果。
  - 输出：
      1) 90 条标注错误的语义 top-3 建议（写入 data/validation/_semantic_calibration.txt）
      2) 人工校正子集上的 top-1/top-3 召回与阈值扫描（打印到 stdout）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.analyzers.embedder import get_embedder
from src.analyzers.normalize import (
    build_l3_mapping,
    load_definitions,
    load_hierarchy,
    map_l3_to_path,
)

SAMPLE_JSON = ROOT / "data" / "validation" / "_verify_sample.json"
RESULTS_JSON = ROOT / "data" / "validation" / "_verify_results.json"
OUT_TXT = ROOT / "data" / "validation" / "_semantic_calibration.txt"

# 人工判定「短语 -> 正确 L3」的子集（只覆盖能明确判定的错误）。
# 用于量化语义匹配的召回，不代表全量正确标签。
KNOWN_CORRECTIONS: dict[str, str] = {
    "挂真的太多": "外挂",
    "挂太多": "外挂",
    "晚上全是挂哥们": "外挂",
    "排位赛挂太多": "外挂",
    "官匹优先模式AI和挂满天飞": "外挂",
    "挂更多了还绿不了": "外挂",
    "真的遇到挂逼又封不掉": "外挂",
    "乱封号": "外挂",
    "韩国人带头盗号": "外挂",
    "板子横行战场": "外挂",
    "有快五年fps基础都要被各路挂钩": "外挂",
    "GUA TA太多了": "外挂",
    "写作水平优秀": "主线",
    "家庭伦理就别来了": "主线",
    "全篇充满着谜语人": "主线",
    "大卫你不要死啊！！！！！！！": "主线",
    "康纳死在楼里": "主线",
    "芥个康纳很萌": "主线",
    "卡拉也成功逃到了加拿大": "主线",
    "卡拉的逃亡、康纳的挣扎、马库斯的抗争，三条线紧紧缠绕": "主线",
    "卡拉线的每一次抉择都很煎熬，基本要直面我的道德底线": "主线",
    "操控微觉滞涩": "打击感",
    "可玩性差": "重玩价值",
    "可玩性还是可以的": "重玩价值",
    "我的电脑已经到极限了": "配置要求",
    "撒比太多": "玩家氛围",
    "神人一堆": "玩家氛围",
    "把把有这种SB": "玩家氛围",
    "种族歧视一大堆": "玩家氛围",
    "玩家越来越少": "玩家氛围",
    "国际服全是超雄老外": "玩家氛围",
    "環境特別好": "玩家氛围",
    "天梯积分实力失真": "匹配",
    "低分局天梯积分增幅加多减少": "匹配",
    "打个死斗全是人机": "匹配",
    "卡关了就去看看": "关卡设计",
    "空气墙以及第六章地图空旷问题存在缺陷": "关卡设计",
    "40块完全版绝品": "性价比",
    "这个价格这个音画表现玩了就不亏了": "性价比",
    "进不去": "服务器",
    "好像走进一部电影中": "动画演出",
    "丹德莱恩的名字好贴这个人设": "角色塑造",
}


def _build_index(embedder, defs: dict) -> tuple[list[str], np.ndarray]:
    """把每个 L3 的「名称 + 定义」编码为归一化向量，返回 (l3_names, matrix)"""
    names = []
    texts = []
    for l3, item in defs.items():
        definition = (item.get("definition") or "").strip()
        names.append(l3)
        # 名称与定义拼接，让"外挂""主线"等标签词本身也参与语义
        texts.append(f"{l3}：{definition}" if definition else l3)
    matrix = embedder.encode_batch(texts)  # 已 L2 归一化
    return names, matrix


def _topk(embedder, names: list[str], matrix: np.ndarray, phrase: str, k: int = 3):
    qv = embedder.encode(phrase)
    sims = matrix @ qv
    order = np.argsort(sims)[::-1][:k]
    return [(names[i], float(sims[i])) for i in order]


def main() -> None:
    parser = argparse.ArgumentParser(description="bge 语义匹配校准（阶段1 诊断）")
    parser.add_argument("--check-only", action="store_true", help="只校验数据与人工校正子集，不加载模型")
    args = parser.parse_args()

    defs = load_definitions()
    mapping = build_l3_mapping(load_hierarchy())
    sample = json.loads(SAMPLE_JSON.read_text(encoding="utf-8"))
    results = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    errors = [s for i, s in enumerate(sample) if results[str(i)][0] == 0]

    l3_names = set(defs.keys())
    err_phrases = {s["phrase"] for s in errors}
    unmatched = [p for p in KNOWN_CORRECTIONS if p not in err_phrases]
    bad_l3 = [v for v in KNOWN_CORRECTIONS.values() if v not in l3_names]

    print(f"样本总数：{len(sample)}  错误样本数：{len(errors)}")
    print(f"L3 定义数：{len(defs)}  人工校正子集：{len(KNOWN_CORRECTIONS)}")
    if unmatched:
        print(f"[WARN] 校正短语未命中错误样本 {len(unmatched)} 条：{unmatched}")
    if bad_l3:
        print(f"[WARN] 非法校正 L3 名 {len(bad_l3)} 个：{bad_l3}")

    if args.check_only:
        if unmatched or bad_l3:
            print("[FAIL] 数据校验未通过。")
            sys.exit(1)
        print("[OK] 数据校验通过。可在装有 torch 的环境去掉 --check-only 运行完整校准。")
        return

    embedder = get_embedder()
    if embedder is None:
        print("[SKIP] sentence-transformers 或模型不可用，无法运行语义校准。")
        sys.exit(0)

    names, matrix = _build_index(embedder, defs)

    print(f"模型：{embedder.model_name}  dim={embedder.dim}")

    # 1) 90 条错误的语义 top-3 明细 → 文件
    lines = []
    for s in errors:
        top = _topk(embedder, names, matrix, s["phrase"], k=3)
        parts = " | ".join(
            f"{map_l3_to_path(l3, mapping)} ({score:.3f})" for l3, score in top
        )
        lines.append(f"{s['phrase']}\n  当前: {s['full_path']}\n  语义: {parts}\n")
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines), encoding="utf-8")
    print(f"90 条错误语义建议已写入：{OUT_TXT}")

    # 2) 人工校正子集：正确 L3 的排名 / 分数
    phrase_by_key = {s["phrase"]: s for s in sample}

    # 预计算每个校正样本：top-1 标签、正确标签的排名与分数
    eval_rows = []
    for phrase, expected in KNOWN_CORRECTIONS.items():
        if phrase not in phrase_by_key:
            continue
        if expected not in names:
            print(f"[WARN] 校正标签 {expected} 不在 L3 词表，跳过：{phrase}")
            continue
        sims = matrix @ embedder.encode(phrase)
        order = np.argsort(sims)[::-1]
        top1 = names[order[0]]
        rank = int(np.where(order == names.index(expected))[0][0]) + 1
        score = float(sims[names.index(expected)])
        eval_rows.append((phrase, expected, top1, rank, score))

    ranks = [r[3] for r in eval_rows]
    scores_of_expected = [r[4] for r in eval_rows]
    top1_hits = [1 for r in eval_rows if r[2] == r[1]]
    n = len(eval_rows)
    print(f"\n=== 人工校正子集（{n} 条）正确 L3 的语义表现 ===")
    print(f"top-1 命中率：{len(top1_hits)}/{n} = {len(top1_hits)/n:.1%}")
    print(f"top-3 命中率：{sum(r <= 3 for r in ranks)}/{n} = {sum(r <= 3 for r in ranks)/n:.1%}")
    print(f"平均排名：{np.mean(ranks):.1f}  中位排名：{np.median(ranks):.0f}")
    print(f"正确 L3 相似度：min={min(scores_of_expected):.3f} 均值={np.mean(scores_of_expected):.3f} max={max(scores_of_expected):.3f}")

    # 3) 阈值扫描：top-1 且相似度 >= t 才算命中
    print(f"\n=== 阈值扫描（top-1 且 score>=t）===")
    for t in np.arange(0.30, 0.71, 0.05):
        hits = sum(1 for _, expected, top1, _, score in eval_rows if top1 == expected and score >= t)
        print(f"  threshold={t:.2f}  召回={hits}/{n} = {hits/n:.1%}")


if __name__ == "__main__":
    main()
