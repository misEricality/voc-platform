# 🛠️ 脚本索引（scripts/）

> **运维/调试/数据处理脚本地图** — 区分"一次性的开发脚本"与"长期运行的运维脚本"。
>
> **最后更新**：2026-08-23（新增 `ops/archive_online_games.py` + 注册 `src.queue` 项目级 CLI）

---

## 🗺️ 脚本地图

```
scripts/
├── README.md                       ⬅ 你在这里
├── smoke_test.py                   ✅ 项目骨架冒烟测试（长期保留，CI 用）
├── dev/                            🧪 开发期一次性脚本
│   ├── 采集与验证                      batch_collect_recent / backfill_0816 / collect_6_games / verify_*
│   ├── 数据巡检与修复                   db_stats / inspect_aug3_data / cleanup_cs2 / debug_*
│   ├── 标注管线（方案4）               reanalyze_all / reanalyze_outliers / gen_l3_definitions
│   │                                   migrate_opinions_v2 / dump_opinions / export_xlsx
│   │                                   write_completion_flag / curate_l3_definitions
│   │                                   rebuild_golden_set / clean_old_labels / recompute_topics
│   │                                   stage1_report / rematch_opinions / mine_fallback_candidates
│   │                                   select_random500 / export_sample_xlsx
│   │                                   export_validation_sample
│   ├── 诊断对比                         diag_batch_vs_single / diag_prompt_a / verify_config
│   ├── B 站探针（2026-08-13）           probe_bilibili / probe_bili_wbi / probe_bili_ticket
│   │                                   diag_bili_412
│   ├── 原型构建                         build_prototype / export_prototype_data / subset_font
│   ├── L3.5 微话题下钻                  l35_cluster
│   ├── 数据修复                         backfill_steam_target_names
│   ├── P6 收口一次性（2026-08-22）       setup_p6_bootstrap（建 voc-daily-bootstrap release + git push）
│   └── E2E 验证                         e2e_lifecycle
├── analysis/                       🔬 分析脚本（ad-hoc 探索）
└── ops/                            ⚙️ 运维脚本
    ├── refresh_likes.py                ✅ 7 天后回采脚本（已实现）
    ├── backfill_embeddings.py          ✅ 评论向量回填 / 换模型全量重算（已实现）
    ├── daily_incremental_collect.py    ✅ P6 每日增量采集编排入口（GitHub Actions 调用）
    └── archive_online_games.py         ✅ 一次性：4 款 Steam 网游数据归档 + 主库清理（2026-08-23）
```

---

## 📌 各脚本用途

### 冒烟 / 运维

| 脚本 | 何时使用 | 备注 |
|------|----------|------|
| **smoke_test.py** | 每次新增模块后跑一次 | 项目骨架回归测试 |
| **ops/refresh_likes.py** | 发布满 7 天的评论回采点赞/回复/开发者回复 | `python -m scripts.refresh_likes --platform steam --target <appid>` |
| **ops/backfill_embeddings.py** | 评论语义向量回填 / 换模型全量重算 | `python scripts/ops/backfill_embeddings.py --limit 100`（增量）；`--force`（清空重算，单事务原子切换） |
| **ops/daily_incremental_collect.py** | P6 每日增量采集编排入口（GitHub Actions 调） | `python scripts/ops/daily_incremental_collect.py`（默认全流程）；`--no-download --no-upload`（本地调试） |
| **ops/archive_online_games.py** | 一次性：把 4 款 Steam 网游（PUBG/Apex/Dota2/CS2）数据从主库抽到 `data/archive/online_games_YYYY-MM-DD.db`，并从主库删除（2026-08-23 已执行） | `python scripts/ops/archive_online_games.py --dry-run`（预览）；不带参数实际执行；归档后主库 VACUUM |

### 项目级 CLI（src/queue · B 站采集队列）

| 子命令 | 用途 |
|---|---|
| `python -m src.queue add BV [BV ...]` | 录入 BV 号到待采清单（自动识别 pubdate） |
| `python -m src.queue list [--status X]` | 列出条目（默认全部） |
| `python -m src.queue due [--limit N]` | 列今天到期的任务 |
| `python -m src.queue run-due [--limit N] [--dry-run]` | 立即触发今天的采集（本地调试 / workflow cron 都用） |
| `python -m src.queue skip BV --reason X` | 跳过某个 BV（标记 failed） |
| `python -m src.queue remove BV` | 删除条目（仅 pending/scheduled/failed） |
| `python -m src.queue show BV` | 显示详情（JSON） |

详见 [architecture/BILIBILI_AUTOMATION.md](../docs/architecture/BILIBILI_AUTOMATION.md)。

### dev/ · 采集与验证

| 脚本 | 用途 |
|------|------|
| `batch_collect_recent.py` | 近期评论批量采集（时间窗） |
| `backfill_0816.py` | 补齐评论到 2026-08-16（Steam 10 款 08-04→08-16 时间窗 + B站全量重采去重；`--dry-run` 预览 / `--platform` 选择） |
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
| `backfill_steam_target_names.py` | 回填 Steam 历史评论缺失的 `target_name`（extra_meta.name 为空时按 appid 兜底） |

### dev/ · 标注管线（方案4）

| 脚本 | 用途 |
|------|------|
| **`reanalyze_all.py`** | 核心：全量重打标（观点短语 → 程序匹配 + 三轮收敛），`--limit 200 --random` 抽样可复现 |
| `reanalyze_outliers.py` | 只重打"未匹配/无观点"的离群评论 |
| `gen_l3_definitions.py` | 生成 L3 定义词典（早期版本；GDT v3.1.1 后由 `curate_l3_definitions.py` 重建） |
| `curate_l3_definitions.py` | 精编 GDT v3.1.1 的 111 个 L3 关键词词典（按新词表重建 `l3_definitions.yaml`） |
| `mine_fallback_candidates.py` | 挖掘兜底词典缺口候选：扫"总体体验评价"桶，自动过滤合理整体评价/噪音，按出现次数列出疑似具体话题短语供人工加词；`--out` 导出 Markdown 待办（新数据落库后跑） |
| `migrate_opinions_v2.py` | opinions 表结构迁移（v1 → v2） |
| `dump_opinions.py` | 导出观点明细检查匹配质量 |
| `export_xlsx.py` | 导出标注结果为 xlsx（comments + opinions 双 Sheet；v3.1.1 起不再导出 `sub_topics` / `content` 冗余列，`target_name` 缺失按 appid 兜底） |
| `write_completion_flag.py` | 收敛完成后写 `data/analysis_done.flag` + 输出报告 |
| `clean_old_labels.py` | 旧标签清洗（v3.0 L1×7 → v3.1.1 L1×10）：把 `comments.topic` 与 `comment_opinions.full_path` 的旧标签名迁移到新词表；幂等，可重打前后各跑一次 |
| `recompute_topics.py` | topic 兜底下沉：把 `comments.topic` 从「综合与元表达」锚点下沉到具体 L1（只处理兜底 topic 且已有具体观点的评论，不重跑 LLM，幂等） |
| `rematch_opinions.py` | 存量重匹配：用当前词典对 `comment_opinions.quote` 重跑 `match_l3`，只把「兜底→具体」写回 `full_path`（不重跑 LLM，幂等；`--dry-run` 预览） |
| `stage1_report.py` | P9 阶段1 收口报告：topic/opinion 两级兜底占比 + 战斗/动作/文化等词典命中统计 |
| `rebuild_golden_set.py` | 按 GDT v3.1.1 重建黄金集并生成 pytest fixture（`tests/fixtures/golden_match_set.json`）；人工校正项读 `tests/fixtures/golden_overrides.json` |
| `select_random500.py` | 复现 `reanalyze_all.py` 的随机抽样，固定 500 条重打样本 ID |
| `export_sample_xlsx.py` | 按固定评论 ID 列表导出重打结果为 xlsx |
| `export_validation_sample.py` | 从打标结果 xlsx 提取 500 条抽样验证样本（供黄金集重建） |

### dev/ · 诊断对比 / 原型

| 脚本 | 用途 |
|------|------|
| `diag_batch_vs_single.py` | 批量 vs 单条打标质量对比（方案4 选型依据） |
| `diag_prompt_a.py` | prompt A（强制枚举 L3）缺陷诊断 |
| `verify_config.py` | 配置（gaming.yaml / l3_definitions.yaml）加载验证 |
| `build_prototype.py` | 组装 v3 单文件自包含原型（内嵌子集字体 + logo base64，源文件含 `page.html`） |
| `export_prototype_data.py` | 导出原型所需数据（单个 JSON） |
| `subset_font.py` | OPPO Sans 字体子集化（仅保留原型实际用到的字符，供 `build_prototype.py` 内嵌） |
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

### dev/ · L3.5 动态微话题下钻（2026-08-17）

| 脚本 | 用途 |
|------|------|
| `l35_cluster.py` | 手动触发的本地 bge 聚类，输出簇的样本评论 ID + 代表短语；窗口样本 <30 条时告警。需 ML 环境（见 `docs/guides/SETUP_ML_ENV.md`） |

> 正式采集器在 `src/collectors/bilibili.py`（已实现并实测：BV1UpwaeNESx 全链路落库，1006 条评论入库）。

---

## 🎯 何时新增脚本

| 新增类型 | 归类到 |
|----------|--------|
| 一次性验证某功能 | `dev/` |
| 每天/每周跑一次的运维任务 | `ops/` |
| ad-hoc 数据探索（看一次就丢） | `scripts/analysis/`（`notebooks/` 已移除） |
| 长期保留的回归测试 | `tests/`（不在 scripts/） |

---

## ⚠️ 约束

- `scripts/` 下所有脚本必须能从项目根目录直接运行：`python scripts/xxx.py`
- 一次性脚本超过 3 个月无人使用，建议归档到 `dev/archive/` 或删除

---

## 📋 版本记录

| 更新时间 | 内容 | 原因 |
|---|---|---|
| 2026-08-19 | 新建 `ops/daily_incremental_collect.py`：GitHub Actions 每日调用的增量采集编排入口；同步登记 ops 章节 | P6 自动化流水线落地 |
- 不要把 prompt 模板或业务配置写死在脚本里，统一从 `config/` 加载
