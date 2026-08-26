# DEEPSEEK vs QWEN-flash 双标注对比报告

- 抽样时间: 2026-08-25T15:38:03.847662Z
- 抽样数量: 198
- DEEPSEEK 版本: `llm:deepseek-v4-flash@(pre-analyzer-version)`（如有多个会显示不同）
- QWEN-flash 版本: `N/A`

## 一致率

| 维度 | DEEPSEEK → QWEN-flash 一致率 |
|---|---|
| sentiment (positive/negative/neutral) | **198/198 = 100.0%** |
| topic (一级标签) | **198/198 = 100.0%** |
| sentiment_score 平均 |Δ| | **0.000** |

## sentiment 分布

| sentiment | DEEPSEEK | QWEN-flash |
|---|---|---|
| positive | 159 | 159 |
| negative | 22 | 22 |
| neutral | 17 | 17 |

## 分歧 sample（前 10 条 sentiment 或 topic 不一致的）

（全部一致，0 分歧）