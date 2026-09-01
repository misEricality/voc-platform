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
| `scripts/dev/mine_fallback_candidates.py` | 词典缺口挖掘：扫兜底桶，列疑似具体话题短语供人工加词 |
| `scripts/dev/rematch_opinions.py` | 存量重匹配：用新词典重跑 match_l3，把「兜底→具体」写回 full_path |
| `scripts/dev/recompute_topics.py` | topic 兜底下沉：把 comments.topic 从兜底锚点下沉到具体 L1 |
| `scripts/dev/archive/one_shot_curate/stage1_report.py` | P9 阶段1 收口报告：topic/opinion 两级兜底占比（2026-09-01 归档） |
| `scripts/dev/archive/one_shot_backfill/clean_old_labels.py` | 旧标签清洗（v3.0 → v3.1.1 标签名迁移；2026-09-01 归档） |
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

## 词典维护闭环（2026-08-18 沉淀）

新数据落库后，按固定节奏维护词典——不是每次标注都扩，只在兜底桶出现「疑似具体话题漏网」时才做：

```
新数据落库 → mine_fallback_candidates.py（挖候选）
          → 人工精编（加词到 l3_definitions.yaml / normalize.py 的 RECOMMEND_WORDS / MEME_STRONG_KEYWORDS）
          → pytest tests/test_golden_match.py（黄金集门禁）
          → rematch_opinions.py（存量重匹配：兜底→具体写回）
          → recompute_topics.py（topic 下沉）
          → stage1_report.py（量兜底占比）
```

- 挖矿原则：只挖「长度 ≥6、含具体话题、非整体褒贬、非数字/拼音噪音」的短语；加词前人工消歧，单字 / 跨域歧义词不入库。
- 门禁原则：任何词典 / 规则改动必须过黄金集回归（410 条），防止改坏已正确标注。

## 已知局限

- **兜底桶承载仍偏重**（2026-08-17 验证样本 L3 严格准确率 76.6%、兜底 71.9%；2026-08-18 扩充战斗/动作/文化/反作弊等 30 词并做存量重匹配后，全库 topic 兜底 67.6% / opinion 兜底 67.4%）：兜底大头是"好玩/垃圾/神作"这类合理整体评价，词典只能救长尾具体短语（约 1-2%）；要把兜底压到 ≤30% 还需观点提取 prompt 调优 + 指标口径（整体印象单列下钻），非词典单方面可解
- **bge 语义匹配已证伪**（2026-08-16 校准）：`bge-small-zh` 对游戏黑话/专名 top-1 仅 19%、正确 L3 平均排名第 25，不足做 L3 兜底；因此阶段 1 不引入语义匹配
- **黄金集回归门禁已落地**：410 条人工确认样本（`tests/fixtures/golden_match_set.json`）保护已正确标注样本不被词典/规则改动破坏
- **定义词典需持续维护**：CS2 黑话（挂壁/绿玩/开黑）、影之刃战斗/武侠表达需人工补充
- **单字关键词有误伤风险**：已移除"挂""透""乐"等单字；`DISAMBIGUATION_SUBSTRINGS` 专名消歧位已预留（当前为空）

## 运行

```bash
# 抽样 200 条（seed=42 可复现）
python scripts/dev/reanalyze_all.py --limit 200 --random

# 全量（9000+ 条）
python scripts/dev/reanalyze_all.py

# 导出 xlsx 检查
python scripts/dev/archive/one_shot_export/export_xlsx.py --only-with-opinions --out data/exports/先导出.xlsx

# 黄金集回归门禁（任何词典/匹配规则改动后必跑）
pytest tests/test_golden_match.py

# 词典维护闭环（新数据落库后）
python scripts/dev/mine_fallback_candidates.py --out candidates.md   # 挖候选
python scripts/dev/rematch_opinions.py --dry-run                     # 预览重匹配
python scripts/dev/rematch_opinions.py                               # 存量重匹配（兜底→具体写回）
python scripts/dev/recompute_topics.py                               # topic 下沉
python scripts/dev/archive/one_shot_curate/stage1_report.py         # 量兜底占比
```
