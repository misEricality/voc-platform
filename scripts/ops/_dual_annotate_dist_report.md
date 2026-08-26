# DEEPSEEK vs QWEN-flash 分布级对比报告（方案 A）

- 生成时间: 2026-08-26T07:09:22.056331
- DEEPSEEK 数据: 8254 条（bootstrap 时期，analyzer_version 字段未启用）
- QWEN-flash 数据: 2542 条（run #24 今天标注）
- 数据范围: 6 款 Steam 单机 + bilibili:video:115581428696874 (历史已归档)
- 同分布基础: 同 target_id 集合 + 同 schinese 语言 + 同posted_at 窗口

## sentiment 分布

| sentiment | DEEPSEEK | QWEN-flash | Δ |
|---|---|---|---|
| positive | 4,831 (58.5%) | 0 (0.0%) | **-58.5pp** |
| negative | 2,189 (26.5%) | 0 (0.0%) | **-26.5pp** |
| neutral | 1,234 (15.0%) | 2,542 (100.0%) | **+85.0pp** |

## topic top 10

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

## sentiment_score 分布

| | avg | min | max | avg_conf | n |
|---|---|---|---|---|---|
| DEEPSEEK | +0.273 | -1.000 | +1.000 | 0.790 | 8,254 |
| QWEN-flash | +0.000 | +0.000 | +0.000 | 0.000 | 2,542 |

## 解读

- **positive 占比**：DEEPSEEK 倾向给『好』，QWEN-flash 倾向更中性 → 注意 DEEPSEEK 标 positive 比例远高于 QWEN-flash 时
- **topic 分布**：看是否有大 topic（如『游戏性』）一边多一边少
- **sentiment_score**：DEEPSEEK 越接近 ±1 越极端，QWEN-flash 越接近 0 越保守

## 结论建议

- 如果 sentiment 分布 Δ 在 ±5pp 内、topic 一致 → QWEN-flash 可作 backup
- 如果 sentiment 分布 Δ > 10pp → 需回 DEEPSEEK，或针对特定 game 调 prompt
- 如果 score 均值差 > 0.15 → 模型倾向差异显著，需深入 sample