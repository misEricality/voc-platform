# P11 (former) · QWEN-flash 双标注实验报告（2026-08-25 / 2026-08-26）

> **归档原因**（2026-08-27 洁癖收口）：3 个候选 QWEN-flash 模型名全部 API 404，路径已弃用；
> 脚本 `scripts/ops/dual_annotate_qwen_flash.py` 保留（备重生路径），产物文件已归档。
>
> **关联**：[.workbuddy/memory/2026-08-25.md](../../.workbuddy/memory/2026-08-25.md) · [AGENTS.md §6](../../AGENTS.md) 2026-08-25 版本行
> **生成来源**：原始报告由 `scripts/ops/dual_annotate_qwen_flash.py` 跑出，重命名归档后内容不变。

---

## 1. 一致率报告（方案 B：抽样 198 条）

**原文件**：`scripts/ops/_dual_annotate_report.md`（已删除）

- **抽样时间**：2026-08-25T15:38:03.847662Z
- **抽样数量**：198
- **DEEPSEEK 版本**：`llm:deepseek-v4-flash@(pre-analyzer-version)`（早于 P10 字段启用）
- **QWEN-flash 版本**：N/A

| 维度 | 一致率 |
|---|---|
| sentiment（positive/negative/neutral） | **198/198 = 100.0%** |
| topic（一级标签） | **198/198 = 100.0%** |
| `sentiment_score` 平均 `|Δ|` | **0.000** |

### sentiment 分布

| sentiment | DEEPSEEK | QWEN-flash |
|---|---|---|
| positive | 159 | 159 |
| negative | 22 | 22 |
| neutral | 17 | 17 |

### 分歧样本

（前 10 条 sentiment 或 topic 不一致的）—— 全部一致，**0 分歧**

---

## 2. 分布级对比报告（方案 A：2542 条全量）

**原文件**：`scripts/ops/_dual_annotate_dist_report.md`（已删除）

- **生成时间**：2026-08-26T07:09:22.056331
- **DEEPSEEK 数据**：8254 条（bootstrap 时期，analyzer_version 字段未启用）
- **QWEN-flash 数据**：2542 条（run #24 同日标注）
- **数据范围**：6 款 Steam 单机 + `bilibili:video:115581428696874`（已归档）
- **同分布基础**：同 target_id 集合 + 同 schinese 语言 + 同 posted_at 窗口

### sentiment 分布

| sentiment | DEEPSEEK | QWEN-flash | Δ |
|---|---|---|---|
| positive | 4,831 (58.5%) | 0 (0.0%) | **-58.5pp** |
| negative | 2,189 (26.5%) | 0 (0.0%) | **-26.5pp** |
| neutral | 1,234 (15.0%) | 2,542 (100.0%) | **+85.0pp** |

### topic top 10

| topic | DEEPSEEK | QWEN-flash |
|---|---|---|
| 综合与元表达 | 5,695 (69.0%) | 0 (0.0%) |
| 平台与安全 | 819 (9.9%) | 0 (0.0%) |
| 机制与内容 | 466 (5.6%) | 0 (0.0%) |
| 叙事与世界观 | 440 (5.3%) | 0 (0.0%) |
| 技术与性能 | 258 (3.1%) | 0 (0.0%) |
| 视觉与艺术 | 165 (2.0%) | 0 (0.0%) |
| 社区与社交 | 161 (2.0%) | 0 (0.0%) |
| 商业与运营 | 118 (1.4%) | 0 (0.0%) |
| 操控与交互 | 90 (1.1%) | 0 (0.0%) |
| 声音与音频 | 42 (0.5%) | 0 (0.0%) |

### sentiment_score 分布

| | avg | min | max | avg_conf | n |
|---|---|---|---|---|---|
| DEEPSEEK | +0.273 | -1.000 | +1.000 | 0.790 | 8,254 |
| QWEN-flash | +0.000 | +0.000 | +0.000 | 0.000 | 2,542 |

### 解读

- **positive 占比**：DEEPSEEK 倾向给"好"，QWEN-flash 倾向更中性 → DEEPSEEK 标 positive 比例远高于 QWEN-flash
- **topic 分布**：看是否有大 topic（如"游戏性"）一边多一边少
- **sentiment_score**：DEEPSEEK 越接近 ±1 越极端，QWEN-flash 越接近 0 越保守

### 结论建议（原始）

- 如果 sentiment 分布 Δ 在 ±5pp 内、topic 一致 → QWEN-flash 可作 backup
- 如果 sentiment 分布 Δ > 10pp → 需回 DEEPSEEK，或针对特定 game 调 prompt
- 如果 score 均值差 > 0.15 → 模型倾向差异显著，需深入 sample

---

## 3. 教训（写进项目沉淀）

| 教训 | 落点 |
|---|---|
| QWEN-flash 个人 token-plan 模型名 ≠ 阿里通用文档列举名 | `.workbuddy/memory/MEMORY.md` 长期工程护栏 |
| 2542 条 QWEN-flash bogus 数据已污染 DB | `scripts/ops/reset_qwen_flash_bogus.py`（P11 一次性清理工具，2026-08-27 落地）|
| 一致率对比毫无意义（run #24/#25 模型名全 404 直接静默兜底） | QWEN-flash 路径永久下线 |
| 双报告命名违规（不应在 scripts/ops/ 留 `_` 开头的报告） | 本次归档收口 |

---

## 4. 训练样本 dump

**原文件**：`scripts/ops/_dual_annotate_labels.json`（已删除；2184 行 198 条样本 + 推导字段）

如有需要重新跑 `python scripts/ops/dual_annotate_qwen_flash.py compare --sample 198 --out labels.json` 重新生成。

---

📅 本文档由 `.workbuddy/memory/2026-08-25.md` + `.workbuddy/memory/2026-08-27.md` 沉淀合并而来；
📅 2026-08-27 洁癖收口（neat-freak）迁入本归档位置，原 `_` 前缀文件删除。
