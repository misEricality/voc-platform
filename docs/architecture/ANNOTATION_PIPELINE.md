# 标注流程说明（方案4：观点短语 → 程序匹配 · 2026-08-06）

## 一句话

**LLM 自由提取观点短语 → 程序用「标签定义词典」匹配 L3 → 映射完整路径 L1/L2/L3**

## 为什么是方案4

方案 A（强制枚举 L3）被证实有**观点召回缺陷**：LLM 在 128 个词里选标签，多面评论（"打击感超爽但优化太差，卡成PPT，闪退"）只选最显眼的 1-2 个词，其余观点丢失。

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
    1. 整活/梗 强特征（坟头草/绿玩/乐子...）
    2. 关键词包含匹配（定义词典，长词加权）
    3. 整体评价（无具体场景夸/贬：好玩/神作/垃圾...）
    4. ≤20字无关键词 → 整体评价（兜底）
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
| `config/topics/gaming.yaml` | 三级标签体系（L1 7 / L2 28 / L3 128） |
| `config/topics/l3_definitions.yaml` | **128 个 L3 定义 + 关键词**（程序匹配词典） |
| `config/prompts/sentiment_user.txt` | LLM prompt（提取观点短语） |
| `config/prompts/sentiment_user_strict.txt` | 收敛第 2/3 轮（强制观点） |
| `src/analyzers/normalize.py` | **匹配层**：match_l3 / build_keyword_index / map_l3_to_path |
| `src/analyzers/sentiment_llm.py` | 批量打标 + 解析 + 程序匹配 + core 判定 |
| `scripts/dev/reanalyze_all.py` | 三轮收敛主流程 |

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
| 整活/梗 → `其他/整活/梗` | match_l3 强特征 |
| 无具体场景夸/贬 → `其他/整体评价/整体评价` | match_l3 兜底 |
| 无观点评论（乱码/时间纪念）→ opinions 留空 | LLM + 收敛 |
| 匹配不到的 phrase → 观点留空 | match_l3 返回 None |
| 每层不留空（L1/L2/L3 三段） | map_l3_to_path |

## 已知局限

- **整体评价占比偏高且趋势恶化**（2026-08-11 实测 ~45.6%；2026-08-15 复测：topic 口径 67.0%、观点口径 62.4%，其中 96% 为 ≤20 字兜底命中而非词典真实匹配）：根因是 L3 关键词词典对 LLM 短语召回不足 + ≤20 字兜底过宽；计划升级为语义匹配（phrase 与 L3 定义的 embedding 相似度，复用 bge 基建）+ 收紧兜底 + 黄金集回归门禁（见 DEVELOPMENT_PLAN 待办）
- **定义词典需持续维护**：CS2 黑话（挂壁/绿玩/开黑）需人工补充
- **单字关键词有误伤风险**：已移除"挂""透""乐"等单字

## 运行

```bash
# 抽样 200 条（seed=42 可复现）
python scripts/dev/reanalyze_all.py --limit 200 --random

# 全量 2067 条
python scripts/dev/reanalyze_all.py

# 导出 xlsx 检查
python scripts/dev/export_xlsx.py --only-with-opinions --out data/exports/先导出.xlsx
```