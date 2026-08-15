# 🛠️ 脚本索引（scripts/）

> **运维/调试/数据处理脚本地图** — 区分"一次性的开发脚本"与"长期运行的运维脚本"。
>
> **最后更新**：2026-08-11

---

## 🗺️ 脚本地图

```
scripts/
├── README.md                       ⬅ 你在这里
├── smoke_test.py                   ✅ 项目骨架冒烟测试（长期保留，CI 用）
├── dev/                            🧪 开发期一次性脚本
│   ├── 采集与验证                      batch_collect_recent / collect_6_games / verify_*
│   ├── 数据巡检与修复                   db_stats / inspect_aug3_data / cleanup_cs2 / debug_*
│   ├── 标注管线（方案4）               reanalyze_all / reanalyze_outliers / gen_l3_definitions
│   │                                   migrate_opinions_v2 / dump_opinions / export_xlsx
│   │                                   write_completion_flag
│   ├── 诊断对比                         diag_batch_vs_single / diag_prompt_a / verify_config
│   ├── B 站探针（2026-08-13）           probe_bilibili / probe_bili_wbi / probe_bili_ticket
│   │                                   diag_bili_412
│   ├── 原型构建                         build_prototype / export_prototype_data
│   └── E2E 验证                         e2e_lifecycle
└── ops/                            ⚙️ 运维脚本
    ├── refresh_likes.py                ✅ 7 天后回采脚本（已实现）
    └── backfill_embeddings.py          ✅ 评论向量回填 / 换模型全量重算（已实现）
```

---

## 📌 各脚本用途

### 冒烟 / 运维

| 脚本 | 何时使用 | 备注 |
|------|----------|------|
| **smoke_test.py** | 每次新增模块后跑一次 | 项目骨架回归测试 |
| **ops/refresh_likes.py** | 发布满 7 天的评论回采点赞/回复/开发者回复 | `python -m scripts.refresh_likes --platform steam --target <appid>` |
| **ops/backfill_embeddings.py** | 评论语义向量回填 / 换模型全量重算 | `python scripts/ops/backfill_embeddings.py --limit 100`（增量）；`--force`（清空重算，单事务原子切换） |

### dev/ · 采集与验证

| 脚本 | 用途 |
|------|------|
| `batch_collect_recent.py` | 近期评论批量采集（时间窗） |
| `collect_6_games.py` | 6 款 Steam 游戏批量采集（黑神话/巫师3/文明6/底特律/33号远征队/星际拓荒） |
| `verify_appids.py` / `verify_appids_zh.py` | 验证 Steam appid 有效性（含中文名查询） |
| `verify_collect.py` | 采集结果落库验证 |
| `e2e_lifecycle.py` | 首次采集 + 回采全链路 E2E（独立测试 DB，不污染主库） |

### dev/ · 数据巡检与修复

| 脚本 | 用途 |
|------|------|
| `db_stats.py` | 数据库统计（评论数 / 情感分布 / 主题分布） |
| `inspect_aug3_data.py` | 8-03 采集数据巡检 |
| `cleanup_cs2.py` | 清理非中文（`language != schinese`）数据 |
| `check_likes_status.py` | 检查评论 likes 状态分布（冷启动 NULL 语义） |
| `debug_dup_source_id.py` / `debug_pagination_loss.py` / `debug_recent_order.py` | Steam API 翻页/去重/排序 bug 排查 |
| `show_refreshed_sample.py` | 回采样本展示 |

### dev/ · 标注管线（方案4）

| 脚本 | 用途 |
|------|------|
| **`reanalyze_all.py`** | 核心：全量重打标（观点短语 → 程序匹配 + 三轮收敛），`--limit 200 --random` 抽样可复现 |
| `reanalyze_outliers.py` | 只重打"未匹配/无观点"的离群评论 |
| `gen_l3_definitions.py` | 生成 `config/topics/l3_definitions.yaml`（128 个 L3 定义 + 关键词） |
| `migrate_opinions_v2.py` | opinions 表结构迁移（v1 → v2） |
| `dump_opinions.py` | 导出观点明细检查匹配质量 |
| `export_xlsx.py` | 导出标注结果为 xlsx（comments + opinions 双 Sheet） |
| `write_completion_flag.py` | 收敛完成后写 `data/analysis_done.flag` + 输出报告 |

### dev/ · 诊断对比 / 原型

| 脚本 | 用途 |
|------|------|
| `diag_batch_vs_single.py` | 批量 vs 单条打标质量对比（方案4 选型依据） |
| `diag_prompt_a.py` | prompt A（强制枚举 L3）缺陷诊断 |
| `verify_config.py` | 配置（gaming.yaml / l3_definitions.yaml）加载验证 |
| `build_prototype.py` | 以 v1 备份为骨架构建原型 v2 |
| `export_prototype_data.py` | 导出原型所需数据（单个 JSON） |
| `export_cs2.py` | 导出 CS2 评论明细（按字段来源分级标注） |

### dev/ · B 站探针与数据（2026-08-13）

| 脚本 | 用途 |
|------|------|
| `probe_bilibili.py <bvid>` | B 站接口探针：view/tags/reply/弹幕全链路（可用作采集器骨架参考） |
| `probe_bili_wbi.py` | WBI 签名算法实现（UP 主空间接口用；沙箱云 IP 实测 -412） |
| `probe_bili_ticket.py` | bili_ticket 尝试（盐值已过时，仅参考） |
| `diag_bili_412.py` | B 站 412 风控诊断（buvid + 完整头排查） |
| `analyze_danmaku.py` | 弹幕词典匹配分析（弹幕不进 LLM 链路，成本红线）→ L1/L2/观点路径分布 |
| `export_bilibili.py` | B 站评论/弹幕数据导出（`--out 路径` 指定输出） |

> 正式采集器在 `src/collectors/bilibili.py`（已实现并实测：BV1UpwaeNESx 全链路落库，1006 条评论入库）。

---

## 🎯 何时新增脚本

| 新增类型 | 归类到 |
|----------|--------|
| 一次性验证某功能 | `dev/` |
| 每天/每周跑一次的运维任务 | `ops/` |
| ad-hoc 数据探索（看一次就丢） | `analysis/` |
| 长期保留的回归测试 | `tests/`（不在 scripts/） |

---

## ⚠️ 约束

- `scripts/` 下所有脚本必须能从项目根目录直接运行：`python scripts/xxx.py`
- 一次性脚本超过 3 个月无人使用，建议归档到 `dev/archive/` 或删除
- 不要把 prompt 模板或业务配置写死在脚本里，统一从 `config/` 加载
