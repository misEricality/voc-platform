"""L3 标签映射 + 观点短语匹配工具（方案 4 · 2026-08-06）

用途：
1. 构建 L3 → (L1, L2) 映射表（从 gaming.yaml）
2. 加载 l3_definitions.yaml（定义 + 关键词词典）
3. **匹配**：LLM 自由提取的观点短语 → 程序匹配最合适的 L3
   - 优先关键词包含匹配（排除兜底标签：整体评价 / 整活/梗）
   - 整体评价 / 整活/梗 用专用规则判定（避免"评价/欢乐"等泛词误匹配）
   - 匹配不到 → None（观点留空，不入盘）
4. 映射：l3 → 完整路径 "L1/L2/L3"
"""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TOPICS_DIR = PROJECT_ROOT / "config" / "topics"

# 兜底标签（GDT v3.1.1）：不参与关键词匹配（用专用规则判定）
FALLBACK_L3 = {
    "综合推荐度",
    "总体体验评价",
    "评测区排版·字符画",
    "网络梗与段子",
    "反讽·阴阳怪气表达",
}

# 网络梗与段子 判定：强特征词（命中即归网络梗与段子，即使无具体场景）
# 注意：避免单字"乐"（会误伤"音乐/快乐"）；用双字及以上词
MEME_STRONG_KEYWORDS = [
    "坟头草", "玩梗", "整活", "乐子", "抽象", "典中典",
    "绷不住了", "蚌埠住了", "流汗黄豆", "梗小鬼", "整蛊", "乐死",
    "沙雕", "表情包", "段子", "发癫", "逆天",
]
# 整体评价 判定：无具体场景的总体夸/贬（命中才归整体评价）
# 注（2026-08-15 评审）：旧名 OVERALL_POSITIVE 有歧义（列表混含夸/贬词），拆分为
# OVERALL_PRAISE / OVERALL_CRITICIZE 两个列表；OVERALL_POSITIVE 保留为兼容别名
# （match_l3 只做成员判断，行为不变）。
# ⚠️ scripts/dev/analyze_danmaku.py 依据这两个列表做情绪正负粗判，勿混淆成员归属。
OVERALL_PRAISE = ["好玩", "神作", "太棒了", "推荐", "不错", "好评", "绝了", "神", "yyds", "nb", "NB", "牛", "史诗", "天花板", "经典", "完美", "love", "god", "perfect", "great", "amazing", "awesome", "nice", "good", "excellent", "best"]
OVERALL_CRITICIZE = ["垃圾", "烂", "粪作", "毁我", "差评", "不行", "拉胯", "糟糕", "烂作", "拉", "答辩", "trash", "shit", "worst", "bad", "terrible", "awful"]
OVERALL_POSITIVE = OVERALL_PRAISE + OVERALL_CRITICIZE  # 兼容别名（成员集合与旧版一致）

# 推荐意图判定：命中即归「综合推荐度」（区别于无推荐意图的「总体体验评价」）
RECOMMEND_WORDS = ["推荐", "值不值得", "值得买", "值不值", "必买", "千万别买", "非常值得"]

# 消歧：专有名词中的“卡”不是“卡顿”。
# 匹配前先把这些子串替换为占位符，避免子串包含匹配误命中
# （例如《底特律》角色“卡拉”会误命中“卡顿”）。
DISAMBIGUATION_SUBSTRINGS = ()




def load_hierarchy(category: str = "gaming") -> dict:
    """加载三级标签体系 {L1 -> {L2 -> [L3...]}}"""
    path = TOPICS_DIR / f"{category}.yaml"
    if not path.exists():
        return {}
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    return cfg.get("hierarchy", {})


def build_l3_mapping(hierarchy: dict) -> dict[str, tuple[str, str]]:
    """构建 L3 → (L1, L2) 全局映射表

    每个标准 L3 词映射回它的完整路径前缀（L1/L2）。
    """
    mapping: dict[str, tuple[str, str]] = {}
    for l1, subs in hierarchy.items():
        if not isinstance(subs, dict):
            continue
        for l2, l3_words in subs.items():
            if not isinstance(l3_words, list):
                continue
            for w in l3_words:
                mapping[w] = (l1, l2)
    return mapping


def build_l3_set(mapping: dict[str, tuple[str, str]]) -> set[str]:
    """构建合法 L3 集合"""
    return set(mapping.keys())


def is_valid_l3(l3: str, l3_set: set[str]) -> bool:
    """校验 l3 是否在合法词表中"""
    return l3 in l3_set


def map_l3_to_path(l3: str, mapping: dict[str, tuple[str, str]]) -> str | None:
    """L3 → 完整路径（L1/L2/L3）

    Returns:
        "玩法与内容/玩法机制/动作系统"
        l3 不在词表 → None

    特殊：L3 词含斜杠时（如"整活/梗"，L3 与 L2 同名），
    路径用 f"{L1}/{L3}"（L3 内含的斜杠自然构成 L2/L3 两段），
    避免 L1/L2/L3 拼接产生 4 段歧义。
    """
    hit = mapping.get(l3)
    if not hit:
        return None
    l1, l2 = hit
    if "/" in l3:
        # L3 词含斜杠（如"整活/梗"）：路径 = L1/L3（L3 自带 L2/L3 层级），保持 3 段
        return f"{l1}/{l3}"
    return f"{l1}/{l2}/{l3}"


def format_l3_full(hierarchy: dict) -> str:
    """全量 L3 词表格式化为 prompt 注入文本（方案 A 硬约束）

    输出：纯逗号列表（实测：带路径前缀的格式会干扰 LLM 选词，纯列表质量更稳）
      核心玩法, 战斗系统, 技能系统, 动作系统, 游戏系统, 机制规则, ...
    """
    flat: list[str] = []
    for l1, subs in hierarchy.items():
        if not isinstance(subs, dict):
            continue
        for l2, l3_words in subs.items():
            if isinstance(l3_words, list):
                flat.extend(l3_words)
    return ", ".join(flat) if flat else "(无可用 L3 标签)"


# =====================================================================
# 方案 4：观点短语 → 程序匹配 L3
# =====================================================================

def load_definitions(category: str = "gaming") -> dict[str, dict]:
    """加载 l3_definitions.yaml（L3 → {definition, keywords}）"""
    path = TOPICS_DIR / "l3_definitions.yaml"
    if not path.exists():
        return {}
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    return cfg.get("definitions", {}) or {}


def build_keyword_index(
    definitions: dict[str, dict],
) -> dict[str, list[str]]:
    """构建 L3 → 关键词列表（含 L3 名称本身）"""
    idx: dict[str, list[str]] = {}
    for l3, item in definitions.items():
        kws = list(item.get("keywords", []) or [])
        kws.append(l3)  # L3 名称本身也是关键词
        idx[l3] = [k for k in kws if k]
    return idx


def match_l3(
    phrase: str,
    keyword_index: dict[str, list[str]],
    defs: dict[str, dict],
) -> str | None:
    """观点短语 → 匹配最合适的 L3

    策略：
    1. 整活/梗 强特征判定（命中 MEME_STRONG_KEYWORDS → 整活/梗）
    2. 关键词包含匹配：遍历所有 L3 的关键词，统计命中数
       - 排除兜底标签（整体评价/整活/梗）
       - 命中数 >0 的最高分标签胜出（长关键词加权）
    3. 无具体场景总体夸/贬（OVERALL_PRAISE / OVERALL_CRITICIZE 命中且无其他匹配）→ 整体评价
    4. 兜底：短中短语（<=20 字，与下方实现一致）且无任何具体关键词命中 → 整体评价
       （短评多为整体感受；长评需谨慎，可能是有具体话题但词典没覆盖）
    5. 都未命中 → None（观点留空）
    """
    if not phrase or not phrase.strip():
        return None
    p = phrase.strip()

    # 消歧：先屏蔽已知专有名词，避免“卡拉→卡顿”这类子串误命中
    for term in DISAMBIGUATION_SUBSTRINGS:
        p = p.replace(term, "×")

    # 1. 网络梗与段子 强特征
    for kw in MEME_STRONG_KEYWORDS:
        if kw in p:
            return "网络梗与段子"

    # 2. 关键词匹配（排除兜底）
    best_l3: str | None = None
    best_score = 0
    for l3, kws in keyword_index.items():
        if l3 in FALLBACK_L3:
            continue  # 兜底标签用专用规则
        score = 0
        for kw in kws:
            if kw and kw in p:
                # 长关键词加权（更长=更精确）
                score += len(kw)
        if score > best_score:
            best_score = score
            best_l3 = l3

    if best_l3 is not None:
        return best_l3

    # 3. 整体评价：含推荐意图 → 综合推荐度；否则 → 总体体验评价
    for kw in RECOMMEND_WORDS:
        if kw in p:
            return "综合推荐度"
    for kw in OVERALL_POSITIVE:
        if kw in p:
            return "总体体验评价"

    # 4. 兜底：短/中短语无具体关键词 → 总体体验评价
    if len(p) <= 20:
        return "总体体验评价"

    # 5. 未匹配
    return None


def normalize_opinions_v4(
    opinions: list[dict],
    keyword_index: dict[str, list[str]],
    defs: dict[str, dict],
    l3_mapping: dict[str, tuple[str, str]],
) -> list[dict]:
    """方案 4：观点列表加工（匹配 l3 + 映射 full_path）

    Args:
        opinions: [{phrase, sentiment, sentiment_score, is_core}, ...]
        keyword_index: L3 → 关键词列表
        defs: L3 定义
        l3_mapping: L3 → (L1, L2)

    Returns:
        加工后列表（每项含 l3 + full_path；匹配不到的 l3=None）
    """
    result: list[dict] = []
    for op in opinions:
        phrase = str(op.get("phrase", "")).strip()
        if not phrase:
            continue
        l3 = match_l3(phrase, keyword_index, defs)
        new_op = dict(op)
        new_op["phrase"] = phrase
        new_op["l3"] = l3
        new_op["full_path"] = map_l3_to_path(l3, l3_mapping) if l3 else None
        result.append(new_op)
    return result


if __name__ == "__main__":
    # 自测
    h = load_hierarchy()
    mapping = build_l3_mapping(h)
    defs = load_definitions()
    kw_idx = build_keyword_index(defs)

    print(f"定义数: {len(defs)}")
    print()
    print("=== 匹配自测 ===")
    tests = [
        ("打击感超爽", "打击感与震动反馈"),
        ("写作水平优秀", "文案品质"),
        ("挂真的太多", "外挂与作弊现象"),
        ("康纳死在楼里", "主线剧情"),
        ("FPS天花板", "战斗系统"),
        ("你最喜欢的FPS开发者最喜欢的游戏", "战斗系统"),
        ("和朋友开黑", "组队开黑·匹配社交"),
        ("坟头草是绿的也算绿玩吗", "网络梗与段子"),
        ("垃圾游戏", "总体体验评价"),
        ("帧率只有30FPS", "帧率表现(FPS)"),
        ("卡成PPT", "画面卡顿·顿挫"),
        ("闪退好几次", "程序闪退"),
        ("价格太贵", "售价策略"),
        ("40块完全版绝品", "性价比与心理预期"),
    ]
    ok = 0
    for phrase, expect in tests:
        got = match_l3(phrase, kw_idx, defs)
        status = "✓" if got == expect else f"✗ (期望 {expect})"
        if got == expect:
            ok += 1
        print(f"  {phrase:<25} → {got}  {status}")
    print(f"\n通过 {ok}/{len(tests)}")
