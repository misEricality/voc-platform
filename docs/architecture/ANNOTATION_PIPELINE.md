# 标注流程说明（方案4：观点短语 → 程序匹配 · GDT v3.1.1）

## 一句话

**LLM 自由提取观点短语 → 程序用「标签定义词典」匹配 L3 → 映射完整路径 L1/L2/L3**

## 为什么是方案4

方案 A（强制枚举 L3）被证实有**观点召回缺陷**：LLM 在 111 个词里选标签，多面评论（"打击感超爽但优化太差，卡成PPT，闪退"）只选最显眼的 1-2 个词，其余观点丢失。

方案 4 把"选标签"从 LLM 手里拿掉：
- **LLM 只提取观点短语**（不受词表限制，召回全）
- **程序匹配 L3**（用定义词典，可控、可调、可测试）

## 完整流程（每批次 10 条）

```
第 0 步：批量 LLM 打标（10 条/批）
  LLM 自由提取 opinions: [{phrase, sentiment, sentiment_score, is_core}]
  + 评论级 sentiment（供兜底）
    ↓
第 1 步：解析批量输出（index 对齐 + 容错）
    ↓
第 2 步：程序匹配（normalize.match_l3）
  phrase → L3（优先级）：
    1. 网络梗与段子 强特征（坟头草/整活/乐子...）
    2. 关键词包含匹配（定义词典，长词加权）
    3. 综合推荐度（含推荐意图：推荐/值不值得/必买...）
    4. 总体体验评价（无推荐意图的总体夸/贬：好玩/神作/垃圾...）
    5. ≤20字无关键词 → 总体体验评价（兜底）
  匹配不到 → 观点留空（不入盘）
    ↓
第 3 步：三轮收敛（reanalyze_all.py）
  第 1 轮：全部 → 筛选无观点评论
  第 2 轮：strict prompt（强制≥1条观点）→ 筛选
  第 3 轮：strict prompt → 仍无观点 → 留空
    ↓
第 4 步：落盘
  comments: topic(=core的L1) / sentiment(=core观点情感) / score
  opinions: full_path / sentiment(观点情感) / phrase
```

## 核心文件

| 文件 | 职责 |
|---|---|
| `config/topics/gaming.yaml` | 三级标签体系（GDT v3.1.1：L1 10 / L2 28 / L3 111） |
| `config/topics/l3_definitions.yaml` | **111 个 L3 定义 + 关键词**（程序匹配词典） |
| `config/prompts/sentiment_user.txt` | LLM prompt（提取观点短语） |
| `config/prompts/sentiment_user_strict.txt` | 收敛第 2/3 轮（强制观点） |
| `src/analyzers/normalize.py` | **匹配层**：match_l3 / build_keyword_index / map_l3_to_path |
| `src/analyzers/sentiment_llm.py` | 批量打标 + 解析 + 程序匹配 + core 判定 |
| `scripts/dev/reanalyze_all.py` | 三轮收敛主流程 |
| `tests/test_golden_match.py` | 黄金集回归门禁（匹配规则改动必跑） |

## 数据模型

### opinions 表（comment_opinions）

| 列 | 含义 |
|---|---|
| full_path | 完整路径（"玩法与内容/玩法机制/核心玩法"） |
| sentiment | 观点情感（positive/negative/neutral） |
| quote | 观点短语（phrase 落盘） |
| quote_start/end | 在原声中的字符位置 |

### comments 表

| 列 | 含义 |
|---|---|
| topic | 核心观点（is_core）的 L1 |
| sentiment | 核心观点的情感（整体情感） |
| sentiment_score | 核心观点的分数 |

## 关键业务规则

| 规则 | 位置 |
|---|---|
| 观点可跨多个 L1 | LLM 提取不受限 |
| 网络梗与段子 → `综合与元表达/社区梗与反讽/网络梗与段子` | match_l3 强特征 |
| 无具体场景夸/贬 → `综合与元表达/整体印象/总体体验评价` | match_l3 兜底 |
| 含推荐意图的总体评价 → `综合与元表达/整体印象/综合推荐度` | RECOMMEND_WORDS 优先命中 |
| 无观点评论（乱码/时间纪念）→ opinions 留空 | LLM + 收敛 |
| 匹配不到的 phrase → 观点留空 | match_l3 返回 None |
| 每层不留空（L1/L2/L3 三段） | map_l3_to_path |

## 已知局限

- **兜底桶承载仍偏重**（2026-08-17 新标签体系 500 条验证样本：观点级 L3 严格准确率 76.6%，71.9% 落入「总体体验评价」类兜底，其中 155 条有明确细粒度标签可归）：主要缺口是战斗/动作/文化武侠相关词典覆盖不足，需继续扩充
- **bge 语义匹配已证伪**（2026-08-16 校准）：`bge-small-zh` 对游戏黑话/专名 top-1 仅 19%、正确 L3 平均排名第 25，不足做 L3 兜底；因此阶段 1 不引入语义匹配
- **黄金集回归门禁已落地**：410 条人工确认样本（`tests/fixtures/golden_match_set.json`）保护已正确标注样本不被词典/规则改动破坏
- **定义词典需持续维护**：CS2 黑话（挂壁/绿玩/开黑）、影之刃战斗/武侠表达需人工补充
- **单字关键词有误伤风险**：已移除"挂""透""乐"等单字；`DISAMBIGUATION_SUBSTRINGS` 专名消歧位已预留（当前为空）

## 运行

```bash
# 抽样 200 条（seed=42 可复现）
python scripts/dev/reanalyze_all.py --limit 200 --random

# 全量 2067 条
python scripts/dev/reanalyze_all.py

# 导出 xlsx 检查
python scripts/dev/export_xlsx.py --only-with-opinions --out data/exports/先导出.xlsx

# 黄金集回归门禁（任何词典/匹配规则改动后必跑）
pytest tests/test_golden_match.py
```
