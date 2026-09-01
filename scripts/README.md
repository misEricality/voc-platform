# 🛠️ 脚本索引（scripts/）

> **运维/调试/数据处理脚本地图** — 区分"一次性的开发脚本"与"长期运行的运维脚本"。
>
> **最后更新**：2026-09-01（HANDOVER 收口：`scripts/dev/` 激进归档 42 个一次性脚本到 `archive/` 子目录，按 9 类分组；dev/ 保留 11 个核心脚本；`README.md` 同步登记）

---

## 🗺️ 脚本地图

```
scripts/
├── README.md                       ⬅ 你在这里
├── smoke_test.py                   ✅ 项目骨架冒烟测试（长期保留，CI 用）
├── dev/                            🧪 开发期活跃脚本（11 个，hander 真正会用到的工具集）
│   ├── 标注核心                       reanalyze_all / rematch_opinions / recompute_topics
│   │                                   rebuild_golden_set / mine_fallback_candidates（按需跑）
│   ├── 微话题下钻                     l35_cluster（P9 阶段2 骨架）
│   ├── 弹幕分析                       analyze_danmaku（B 站弹幕词典匹配）
│   ├── 原型数据导出                   export_prototype_data（被 product/ 下脚本引用）
│   └── 端到端验证（2026-08-31 新增）   verify_glm_5_3_flash / verify_smart_window_e2e / verify_today_collect
├── dev/archive/                    📦 已完成任务的一次性脚本（42 个，2026-09-01 归档）
│   ├── debug/                           debug_dup_source_id / debug_pagination_loss / debug_recent_order
│   ├── diag/                            diag_batch_vs_single / diag_prompt_a / diag_bili_412
│   │                                     probe_bilibili / probe_bili_wbi / probe_bili_ticket
│   ├── e2e/                             e2e_lifecycle / show_refreshed_sample / check_likes_status / dump_opinions
│   ├── one_shot_backfill/               backfill_0816 / backfill_steam_target_names / cleanup_cs2
│   │                                     clean_old_labels / migrate_opinions_v2 / batch_collect_recent
│   │                                     export_cs2 / inspect_aug3_data / collect_6_games / db_stats
│   │                                     reanalyze_outliers / select_random500
│   ├── one_shot_curate/                 curate_l3_definitions / gen_l3_definitions / gen_prototype_data
│   │                                     stage1_report / write_completion_flag
│   ├── one_shot_export/                 export_bilibili / export_game_compare / export_sample_xlsx
│   │                                     export_validation_sample / export_xlsx
│   ├── one_shot_prototype/              build_prototype / build_game_compare / subset_font
│   ├── one_shot_verify/                 verify_appids / verify_appids_zh / verify_collect / verify_config
│   └── P6_bootstrap/                    setup_p6_bootstrap（已用完，voc-daily-bootstrap 已建）
├── analysis/                       🔬 分析脚本（ad-hoc 探索）
└── ops/                            ⚙️ 运维脚本
    ├── refresh_likes.py                ✅ 7 天后回采脚本（已实现）
    ├── backfill_embeddings.py          ✅ 评论向量回填 / 换模型全量重算（已实现）
    ├── daily_incremental_collect.py    ✅ P6 每日增量采集编排入口（GitHub Actions 调用）
    ├── verify_release_upload.py        ✅ P6 静默失败防御：校验 GH Release asset 上传状态（2026-08-27）
    ├── smart_sync_release.py           ✅ 本地自动 sync GH Release → voc.db（幂等 + 文件锁处理，2026-08-28）
    ├── reset_qwen_flash_bogus.py       ✅ P11 清理 QWEN-flash 404 假数据（dry-run 默认；--commit 真正清，2026-08-27）
    ├── sync_local_from_release.py      ✅ 本地从 GH Release asset 拉 DB 对齐（release 路径，2026-08-24）
    ├── sync_local_from_artifact.py     ✅ 本地从 GH Actions artifact 拉 DB 对齐（artifact 兜底，2026-08-24；当前 release upload bug 期间实际可用路径）
    ├── dual_annotate_qwen_flash.py     ✅ DEEPSEEK vs QWEN-flash 双标注对比（backup + compare 两阶段，2026-08-25；产物已归档）
    ├── archive_online_games.py         ✅ 一次性：4 款 Steam 网游数据归档 + 主库清理（2026-08-23）
    ├── push_via_api.py                 ✅ sandbox 屏蔽 git push 时走 GH REST API 兜底（2026-08-31）
    └── register_sync_tasks.ps1         ✅ Windows Task Scheduler 注册（10:00/13:00/18:00/22:00 sync）
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
| **ops/verify_release_upload.py** | P6 静默失败防御：daily collect 跑完后用 `gh release view` 检查 `voc.db` asset 实际状态（size > 1KB + state=uploaded），失败 exit 1 让 workflow 标红。详见 `docs/architecture/AUTOMATION_PIPELINE.md §8.3` | GH Actions workflow 自动调用；也可 `--tag voc-daily-YYYY-MM-DD` 手动验证；测试：`tests/test_verify_release_upload.py` 8 例 |
| **ops/smart_sync_release.py** | 本地自动 sync GH Release → `data/voc.db`（幂等）：①今天 release 未上传 → 安静 exit 0（专为"10:00 早跑，workflow 还没好"场景设计）②本地比远端新 → noop exit 0 ③远端比本地新 → 下载 + 安全 rename 替换 → exit 0 ④文件锁（Streamlit 打开）→ exit 1 + 提示"关仪表盘" | `python scripts/ops/smart_sync_release.py`（默认 today UTC）或 `--date 2026-08-28` 指定日期。注册到 Windows Task Scheduler 见 `register_sync_tasks.ps1`（4 task 错开 10:00/13:00/18:00/22:00） |
| **ops/register_sync_tasks.ps1** | 注册 Windows Task Scheduler 任务：4 个 daily VOC-Sync-Release-* 任务，分别 10:00 / 13:00 / 18:00 / 22:00，每天跑 `smart_sync_release.py` | **需以管理员身份运行 PowerShell**：`powershell -ExecutionPolicy Bypass -File scripts\ops\register_sync_tasks.ps1`。卸载：`... -Uninstall`。DSH agent 无 admin 权限，不能自动注册 |
| **ops/reset_qwen_flash_bogus.py** | P11 清理 8/24-25 QWEN-flash 模型 404 留下的假数据：UPDATE 261 条 `analyzer_version=llm:qwen3-flash@...` 的评论清掉分析字段，让明早 cron 重新打 | 默认 dry-run 打印预演；`--commit` 真正清；`--like` 宽松匹配（清所有 `llm:qwen%` 假数据） |
| **ops/archive_online_games.py** | 一次性：把 4 款 Steam 网游（PUBG/Apex/Dota2/CS2）数据从主库抽到 `data/archive/online_games_YYYY-MM-DD.db`，并从主库删除（2026-08-23 已执行） | `python scripts/ops/archive_online_games.py --dry-run`（预览）；不带参数实际执行；归档后主库 VACUUM |
| **ops/push_via_api.py** | sandbox 屏蔽 git 出站协议栈时走 GH REST API 推 main 的兜底工具：blob / nested tree / commit / refs 全流程，fast-forward 校验，关键 10 文件 SHA 验证。**推送前必读** `docs/guides/PUSH_TROUBLESHOOTING.md` §1 决策树（sandbox refs 限流时立即停止操作 + 提示手动 git push） | `python scripts/ops/push_via_api.py`（需 `GITHUB_TOKEN` 环境变量，fine-grained PAT `Contents: Read and write`） |

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
| `verify_smart_window_e2e.py` | `daily_incremental_collect.smart_window` v2 端到端验证（mock run_pipeline，看 posted_after/Before 传递是否正确；不联网不污染主库） |
| `verify_glm_5_3_flash.py` | `glm-5.3-flash` provider 接通验证（用真实 key 跑一条样本评论，确认 analyzer_version=llm:glm-5.3-flash@xxx + 标注结果合法；切默认标注器后跑一次回归用） |
| `verify_today_collect.py` | 一键验证今日 workflow 跑通后本地数据（自动 sync release + 检查 posted_at 分布/analyzer_version=v2 时间窗/6 款游戏采集率/情感分布；切默认标注器后验证端到端用） |
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
| 一次性验证某功能 | `dev/`（任务完成后迁到 `dev/archive/<分类>/`，见下） |
| 每天/每周跑一次的运维任务 | `ops/` |
| ad-hoc 数据探索（看一次就丢） | `scripts/analysis/`（`notebooks/` 已移除） |
| 长期保留的回归测试 | `tests/`（不在 scripts/） |
| **dev/archive/ 收纳规则** | 任务完成的一次性脚本移到对应子目录：`debug/`（bug 排查）/ `diag/`（方案选型 / 接口风控）/ `e2e/`（一次性端到端）/ `one_shot_backfill/`（数据回填）/ `one_shot_curate/`（词典 / 报告生成）/ `one_shot_export/`（数据导出）/ `one_shot_prototype/`（HTML 装配）/ `one_shot_verify/`（一次性校验）/ `P6_bootstrap/`（P6 收口自助）。**判断标准**：用一次就不用了 = 归档；可能周期性跑的（如 `mine_fallback_candidates.py`）= 留在 dev/ 根目录 + 顶部加「按需跑」注 |

---

## ⚠️ 约束

- `scripts/` 下所有脚本必须能从项目根目录直接运行：`python scripts/xxx.py`
- 一次性脚本超过 3 个月无人使用，建议归档到 `dev/archive/` 或删除

---

## 📋 版本记录

| 更新时间 | 内容 | 原因 |
|---|---|---|
| 2026-08-19 | 新建 `ops/daily_incremental_collect.py`：GitHub Actions 每日调用的增量采集编排入口；同步登记 ops 章节 | P6 自动化流水线落地 |
| 2026-08-27 | 新建 `ops/verify_release_upload.py` + 8 例 pytest：P6 release upload 静默失败防御；同步新增 workflow 步骤「校验今日 Release asset」；同周新建 `ops/reset_qwen_flash_bogus.py`：P11 清理 8/24-25 QWEN-flash 404 假数据（dry-run 默认；--commit 真正清）；归档 `_dual_annotate_*.{md,json}` 至 `docs/architecture/duals_2026-08-25_archive.md` | 解锁 P6「silent 失败不告警」问题（assets=[] 但 workflow 仍 success）；让日常 cron / 工程师 manual dispatch 都能拿到明确 ❌ 告警；P11 收尾；产物文件原本违规命名（scripts/ops/ 不应放 `_` 开头报告）已纠正 |
| 2026-08-28 | 新建 `ops/smart_sync_release.py`（智能 sync：幂等 + 文件锁处理 + 4 task 错开调度）+ `ops/register_sync_tasks.ps1`（Windows Task Scheduler 注册） | 解锁 P6 「GH Release → 本地」自动 sync（之前需手动跑 sync_local_from_release.py）；4 task 错开应对 8h 延迟；幂等设计支持任意次重跑 |
| 2026-08-31 | 新建 `ops/push_via_api.py`（sandbox 屏蔽 git push 时走 GH REST API 兜底）+ `docs/guides/PUSH_TROUBLESHOOTING.md`（7 章节决策树 + 5 已知坑 + 验证清单，每次 push 前必读） | 解锁 sandbox 推 main 通道；沉淀本次 push 踩的 4 个新坑（REMOTE_HEAD 硬编码 / root 排序 / basename 冲突 / refs 二级限流）+ 历史 1 个（dotfile 404），避免重复踩；与 8-28 §沙箱 push 护栏配套（决策树明示「sandbox refs 限流时立即提示手动 git 推」） |
| 2026-09-01 | **HANDOVER 收口 · dev/ 激进归档 42 个一次性脚本到 `archive/`**：按 9 个子目录分类（debug/ diag/ e2e/ one_shot_backfill/ one_shot_curate/ one_shot_export/ one_shot_prototype/ one_shot_verify/ P6_bootstrap/）；dev/ 保留 11 个核心脚本（reanalyze_all / rematch_opinions / recompute_topics / rebuild_golden_set / mine_fallback_candidates / l35_cluster / analyze_danmaku / export_prototype_data / verify_glm_5_3_flash / verify_smart_window_e2e / verify_today_collect）；ops/ 补 `push_via_api.py` + `register_sync_tasks.ps1`；README 头部时间戳与目录树同步 | 项目交接准备：让新接手者一眼看到「真正活跃的工具集」；冗余一次性脚本不污染日常 dev/ 视角 |
- 不要把 prompt 模板或业务配置写死在脚本里，统一从 `config/` 加载
