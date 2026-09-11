# 灵听 · Lynx · 开发计划与进度速查

> **用途**：项目从 0 到 1 全部里程碑 + 下一步候选 + 当前状态快照。随时查阅。
>
> **关联文档**：
> - 项目说明：[README.md](../../README.md)
> - 5 分钟上手：[QUICK_START.md](../guides/QUICK_START.md)
> - 调研报告：[VOC_COMPETITOR_RESEARCH.md](../research/VOC_COMPETITOR_RESEARCH.md)
> - 字段字典：[DATA_FIELDS.md](../architecture/DATA_FIELDS.md)
> - 存储设计：[DATA_STORAGE_DESIGN.md](../architecture/DATA_STORAGE_DESIGN.md)
> - Steam API 字段：[STEAM_API_FIELDS.md](../architecture/STEAM_API_FIELDS.md)
>
> **最后更新**：2026-09-10（P11 bogus 清理执行 + 计划外进度入库：原声分析 Agent / 静态快照部署 / 每日采集哨兵；测试 219 例）

---

## 📌 一、项目一句话

**让任何非工程师也能 5 分钟从 Steam / B 站 / 微博等公开评论里，看见"用户在吵什么"，并按主题 + 情感 + 时间三个维度看清趋势。**

| 维度 | 内容 |
|---|---|
| 目标用户 | 个人开发者 / 兴趣研究者 / 产品体验探索者 |
| 核心场景 | 游戏评测感受跟踪、品牌口碑聚类、跨平台舆情横向对比 |
| 不做的事 | 商业化大规模抓取、企业级私域数据、实时告警 |
| 预算 | 0 ~ 300 元/年（实测 DeepSeek 千条评论成本 < 1 元） |
| 预计产出 | GitHub 完整工程 + 可分享仪表盘 + 复盘博客 |

---

## ✅ 二、已完成里程碑（按时间倒序）


### M12 · v0.8 Web 实时看板 + 静态快照部署 + 原声分析 Agent（2026-09-02 ~ 2026-09-10）

**已完成**（2026-09-10 补登，此前只散落在 `AGENTS.md` 版本记录里、未进本计划）：
- **Web 实时看板（P，2026-09-02 阶段 1-5 + 09-07 收尾）**：`src/api/` FastAPI（公开只读端点 + 管理员 session 鉴权 + 任务 CRUD）+ `product/web/` 原生 ECharts SPA（单游戏看板 / 游戏对比 / B站视频 / 数据管理 / 系统管理 5 页）+ `collect_tasks` 表 + SQLite WAL；三看板按线框图重写 + 系统管理子模块化（09-03~05）；对抗审查 P1×4 + P2×3 已修
- **原声分析 Agent（2026-09-08 ~ 09-10）**：`src/agent/`（FastAPI SSE + function calling + 4 tool：overview/topics/comments/search_docs）+ `config/agent/`（tools.yaml + skills）+ 前端悬浮球两段式抽屉（小窗 420 / 大窗 900 含历史栏，`#/agent` 一级页已下线归档）+ **引用当前查询**（看板摘要注入上下文，不落库）+ 30 天滚动裁剪（`prune_agent_history.py`，03:30 计划任务）+ Markdown 导出 + 匿名 UUID 隔离；上线后经对抗式审查修 P0×2（chat 越权 fail-open / 伪造 XFF 绕限流）+ P1×2
- **公网部署方案 ③ 静态快照（2026-09-07）**：`ops/export_static_snapshot.py`（复用 service 聚合层导出三页预聚合 JSON）+ `api.js` 静态 shim + `ops/publish_static_snapshot.ps1`（发布 EdgeOne Pages）；公网 live 实测三页 / 降级 / 窗口化全过；手册 `docs/architecture/STATIC_SNAPSHOT_DEPLOYMENT.md`
- **每日采集哨兵（2026-09-07）**：`ops/check_daily_collect.py` + 计划任务 `VOC-Local-Daily-Collect-Check`（03:00 检查 02:00 失败/未跑则补采，并发安全、幂等）；本地直采链路 02:00 → 03:00 兜底
- **评论词云对比（2026-09-08）**：`/api/wordcloud`（jieba + TF-IDF 跨游戏区分度 + 词级情感着色）+ `config/wordlists/wordcloud_stopwords.txt`
- **P11 bogus 清理执行（2026-09-10）**：`ops/reset_qwen_flash_bogus.py --commit` 清掉 2542 条 QWEN-flash 假标注（6 个 Steam 目标）+ `reanalyze_all.py` 补 `analyzer_version` 溯源后重打
- **测试**：49 → **219 例**（agent 64 + API + 快照导出 + 哨兵 + 词云 + 黄金集等）

### M11 · v0.7 分析溯源 + CI pytest（2026-08-21）

**已完成**：
- **analyzer_version 字段溯源**：`comments.analyzer_version`（`{provider}:{model}@{prompt_hash8}`）；LLM/本地 analyzer 都暴露 `analyzer_version` 属性；`compute_prompt_set_hash()` 读 prompt 文件 SHA256 拼 hash，任一 prompt 改动自动联动；`update_analysis` 入参 + 缺省不擦旧值（向后兼容）
- **轻量 schema 演进**：`init_db()` 启动时自动 ALTER 缺列的已存在表（仅 nullable 列；NOT NULL + 非空默认值仍用专用脚本）；老 11332 条评论 analyzer_version 已加列（值 = NULL = 未溯源）
- **CI pytest job**：`.github/workflows/daily-collect.yml` 加 `test` job（与 `collect` 并列），装 `requirements-core.txt` 不装 torch/transformers/sentence-transformers；push/cron 都跑
- **requirements 拆分**：`requirements-core.txt` / `requirements-ml.txt` / `requirements-dashboard.txt` / `requirements.txt`（本地一键全量）
- **测试**：`tests/test_analyzer_version.py` 10 例（字段 / 写入 / 不擦旧值 / prompt hash 稳定 / prompt 变动联动 / LLM format / local format / pipeline 传参 / 缺属性兼容 / init_db 自动 ALTER）全绿

### M8 · v0.4（2026-08-18 ~ 2026-08-19）

**已完成**：
- **数据补采至 2026-08-16**：`backfill_0816.py` 补齐 10 款 Steam 游戏 08-04→08-16 近期评测（Steam 2067 → 8302 条），评论总数 3073 → **9308 条**；向量全量回填 1021 → **9309 条**（单模型 bge-small-zh-v1.5）。**2026-08-23 状态**：原 10 款中 4 款网游（PUBG/Apex/Dota 2/CS2）共 4916 条已归档到 `data/archive/online_games_2026-08-23.db`，主库保留 6 款单机 3,386 条
- **GDT v3.1.1 阶段 1 收口（兜底治理）**：词典扩充 30 词（战斗/动作/文化/反作弊等）+ 存量重匹配 `rematch_opinions.py`（兜底→具体 139 条写回）+ topic 兜底下沉 `recompute_topics.py`（60 条）+ `stage1_report.py` 度量；观点级兜底 71.0% → **67.4%**、topic 兜底 68.6% → **67.6%**
- **词典维护闭环沉淀**：`mine_fallback_candidates.py`（挖兜底缺口候选）+ 黄金集回归门禁（410 条全绿）
- **P3 多目标横向对比（完整落地）**：Streamlit「多目标对比」视图（KPI 表 / 口碑散点 / 情感 100% 堆叠 / 主题×游戏热力图（占比+偏差）/ 负面痛点 TOP5 / 下钻链接）+ 原型卡片页 `game-compare.html`（含「打开分析看板 →」跳转）；设计见 [P3_COMPARE_DESIGN.md](./P3_COMPARE_DESIGN.md)
- **旧标签残留清零**：`clean_old_labels.py` 清洗后 `其他/整体评价/玩法与内容` 等旧标签为 0

### M7 · v0.3（2026-08-13 起，持续演进至 2026-08-17）

**已完成**：
- **B 站采集器**：公开 Web 接口 + 风控适配，评论 1006 条 + 弹幕 1200 条实测落库（`src/collectors/bilibili.py`）
- **语义向量化**：本地 bge-small-zh-v1.5（512 维），`comment_embeddings` 表 + `backfill_embeddings.py` 增量/全量回填
- **仪表盘增强**：情感分布 / 主题 TOP10 / 词云 / 情感分数分布 / 典型样本；支持 Steam / B站 / 全部切换
- **高保真原型 v3**：单文件自包含（内嵌子集字体 + logo base64，`page.html` + 多文件源码）
- **GDT v3.1.1 标签体系（阶段 1 词表落地）**：L1 10 / L2 28 / L3 111，黄金集回归门禁上线；bge 语义匹配证伪；`refresh_likes` 游标续翻与 `posted_at` naive UTC 统一修复
- **品牌升级**：灵听 · Lynx（logo + 标题 + 文档同步）

### M6 · v0.2 数据采集生命周期管理（2026-08-04，commit `ca04681`）

**核心理念转变**：真实的"评论生命周期"中，likes / replies / developer_response 都是**时间敏感字段**——评论刚发布时这些数据全是 0，但 Steam 显示 0 会误导分析（"这条评论没人点赞"≠"这条评论质量差"）。所以 v0.2 重新设计了"冷启动留空 + 7 天后回采"机制。

**核心代码升级**：
- `src/collectors/base.py`：`RawComment.likes / replies` 默认 `None`（语义：尚未回采），而非误导性的 `0`
- `src/collectors/steam.py`：
  - 新增 `filter` 参数（`recent` / `updated` / `all`），明确 Steam API 时间窗语义
  - **翻页去重**：用 `seen_source_ids` 集合规避 Steam API 跨页 `recommendationid` 重复 bug
  - **空页连续终止**：连续 3 页无新数据视为已到时间窗外
  - `fetch_metadata` 开关（首次入库 `False`，7 天后回采 `True`）
  - `posted_after / posted_before` 应用层时间窗过滤
- `src/pipeline.py`：CLI 新增 `--posted-after / --posted-before` 参数
- `src/storage/db.py`：
  - 新增 `likes_refreshed_at` / `developer_response_refreshed_at` 字段
  - 对应复合索引（`posted_at` + `refreshed_at`），支撑回采扫描
  - `upsert` 规则升级：`likes / replies` 为 `None` 不覆盖旧值

**新增 scripts（位于 `scripts/dev/`）：**
- `refresh_likes.py`：发布满 7 天的评论回采点赞/回复/开发者回复（后于 2026-08-07 补全，2026-08-17 修复游标续翻 bug）
- `collect_6_games.py`：6 款 Steam 游戏批量采集（黑神话悟空 / 巫师3 / 文明6 / 底特律 / 光与影 33号远征队 / 星际拓荒）
- `check_likes_status.py`：检查评论 likes 状态分布
- `cleanup_cs2.py` / `inspect_aug3_data.py`：数据清理与巡检
- `debug_dup_source_id.py` / `debug_pagination_loss.py` / `debug_recent_order.py`：翻页 bug 排查
- `verify_appids.py` / `verify_appids_zh.py` / `verify_collect.py`：appid 与采集验证
- `e2e_lifecycle.py`：首次采集 + 回采全链路 E2E 测试
- `show_refreshed_sample.py`：回采样本展示

**新增文档**：
- `docs/architecture/STEAM_API_FIELDS.md`：Steam API 字段大全（设计师对接 + 字段字典）

**当前数据状态**：
- 6 款游戏 × 约 100 条 = **772 条评论** 已在 `data/voc.db`（**未入库**，运行产物）
- **情感分析覆盖 0/772**：因为数据库 schema 升级（`likes` 默认值变更）后，旧分析结果与新 schema 不兼容。这一点必须先解决才能进入 v0.3 仪表盘迭代。

### M5 · v0.1 端到端跑通（2026-07-31 晚）
- 🎉 **从采集到仪表盘的完整链路已贯通**
- 第一批真实数据：**Counter-Strike 2 中文评测 50 条，全部由 DeepSeek 完成情感分析**
- 数据快照：正向 23 / 负向 23 / 中性 4 ｜ CS2 中文社区评价两极化
- 仪表盘在 `http://localhost:8501` 实时可看

### M4 · v0.1 基础版发布（2026-07-31 上午）
- 工程化小修：日期时间模块弃用警告清零、pytest 在 Windows 上不再报错（22 个 warning + 4 个 error → 0/0）
- Smoke Test 一键通过，9 单元测试全绿

### M3 · 仓库上云（2026-07-31）
- GitHub 公开仓库创建（https://github.com/misEricality/voc-platform）
- `.github/workflows/daily-collect.yml` 在本地就绪（未推送，**远端 workflow 缺失**）

### M2 · 调研与设计落地（2026-07-30）
- 完成《VoC 平台竞品调研报告 v2》：
  - 重新定位为**个人学习项目**（非商业）
  - 数据源策略：**官方 API 优先 + 爬虫备用**（合规）
  - 预算：**0-300 元/年**（善用免费额度）
  - 架构：**单机 + Serverless**（彻底摒弃分布式）

### M1 · 项目初始化（已存在）
- 项目骨架、依赖清单、`.env` 模板、基础测试已具备

---

## 📊 三、当前数据快照（2026-09-10）

| 指标 | 数值 | 业务解读 |
|---|---|---|
| 总评论数 | **18,716 条** | Steam 单机 14,475 + B 站 5 视频 4,241 |
| Steam 主库覆盖 | **8 款单机** | 黑神话 / 巫师3 / 文明6 / 底特律 / 33号远征队 / 星际拓荒 / 明末：渊虚之羽 / 鬼武者 |
| Steam 归档库 | **4 款网游 → `data/archive/online_games_2026-09-10.db`（4,916 条 / 7,101 观点 / 4,915 向量，26.2 MB）** | PUBG / Apex / Dota 2 / CS2 + `steam:999` 占位；2026-09-10 **重新归档**（首次 8/23 归档后被远端 DB sync 回灌，已修复）；旧 `online_games_2026-08-23.db` 已于 2026-09-10 删除（经比对是新库的严格子集，0 条独有） |
| B 站支持 | **5 个视频 / 4,241 评论 + 7,359 弹幕** | `bilibili:video:{aid}` 形态 target；本地 `run-due` 每日调度 |
| 情感分析覆盖 | **18,671 / 18,716 = 99.8% 已分析** | 剩 45 条未分析（29 Steam「三轮后无观点」+ 16 B站历史遗留）；主标注器 `llm:deepseek-v4-flash@73892f47`、`glm-5.3-flash@55c003a3` |
| 观点级标注 | **40,404 条** | `comment_opinions` 表（程序匹配观点短语） |
| 语义向量 | **17,713 条** | `comment_embeddings` 表（bge-small-zh-v1.5，全量回填） |
| 已支持游戏/视频 | **8 单机 Steam + 5 B 站视频** | 4 款网游已归档到独立 DB，可单独查询 |
| 数据截至 | 2026-09-10 | 本地直采 02:00 + 03:00 补采哨兵；7 天回看窗口幂等补齐 |
| 兜底占比 | topic 67.6% / opinion 67.4%（**2026-09-10 重打后未复测**） | GDT v3.1.1 锁定；复测脚本已归档到 `scripts/dev/archive/one_shot_curate/stage1_report.py` |
| 主题 TOP1 | 见 DB | L1-L3 三级标签（GDT v3.1.1：L1 10 / L2 28 / L3 111） |
| 部署方式 | **本地直采 + Web 实时看板（uvicorn :8000）+ 公网静态快照（EdgeOne Pages，每日 04:30 自动发布）+ VPS 只读服务（①b，内测上线）** | FastAPI + 原生 SPA 5 页 + Agent 抽屉；Streamlit 并存；方案 ③ 已上线并自动化（`VOC-Local-Publish-Snapshot`）；方案 ①b 已落地：VPS `voc-web.service` + Caddy `:8443` 内测，DB 每日随 02:00 采集推库（`--push-db`） |
| 平台覆盖 | **Steam（单机 8）+ B站（5 视频）** | 微博为下一主扩展 |
| 数据存储 | SQLite 单文件（`data/voc.db`，**117.2 MB / 2026-09-11**）| WAL 模式；前端服务读 × cron 写并发；GH Actions 采集 workflow **已删除**（2026-09-11，仓库仅留 `ci.yml` 回归门禁）；①b 每日 `VACUUM INTO` 快照推 VPS（远端原位 `.backup()`） |
| 测试门禁 | **pytest 236 例** | 黄金集 + API + 队列 + 每日采集（含推库 8 例）+ 哨兵 + monitored 白名单 + Agent |

---

## 🗺️ 四、当前路线图（按推荐优先级排列）

> 排序逻辑：**业务价值 / 工作量** 比 + **对项目作品集吸引力** 增量 + **避开过度工程化**。
>
> 标记说明：🔴 阻塞 ｜ 🥇 强烈推荐 ｜ 🥈 锦上添花 ｜ 🥉 视心情 ｜ ⏸️ 可放缓

---

### ✅ P0 · 修复情感分析覆盖（已完成 2026-08-06，方案4 全量重打）

> **当前状态**：2067/2067 评论已分析，`comment_opinions` 观点级标注 295 条。本节保留为历史记录。

**问题**：v0.2 schema 升级后，`likes` 字段从 `default=0` 改为 `nullable=True`。column 默认值变化让 SQLAlchemy 触发了 metadata 重新生成，但旧数据行的 `analyzed_at` 字段未受影响——所以 `analyzed = 0` 的根本原因不是 schema 冲突，而是**没有触发过分析 pipeline**（新 comment 数 772，是 v0.1 的 50 条评论重跑分析后口径未更新）。

**业务目标**：让仪表盘重新"有数据可看"。

**交付物**：
- 跑一次 `python -m src.pipeline --platform steam --target <6 个 appid> --count 500 --language schinese`（含分析）
- 验证 `analyzed == 772` 后才能进入后续路线
- 成本预估：DeepSeek 分析 772 条 ≈ ¥2-5（仍在预算内）

**为什么排第一**：
- 不修复这条，后面所有 P1-P3 仪表盘迭代都是"在空数据上画图"

---

### ✅ P1 · 主题分类精细化（已由 GDT v3.1.1 落地，2026-08-17）

**问题（历史）**：主题分类曾把所有内容堆到"游戏性"里，仪表盘看不出玩家具体在抱怨什么。

**现状**：已从旧的 L1 7 / L2 28 / L3 128 升级到 GDT v3.1.1 的 **L1 10 / L2 28 / L3 111**；`config/topics/gaming.yaml` 与 `l3_definitions.yaml` 已重建，`normalize.py` 程序匹配层同步更新。全量重打与旧标签清洗仍待收口（见 P9 阶段 1 与阻塞表）。

---

### ✅ P2 · 仪表盘评论词云（核心已完成）

**现状**：`app.py` 已集成词云（jieba 分词 + `wordcloud` + 中文字体自动探测），与情感分数分布并列展示。

**待增强（非阻塞）**：词云按「正向 / 负向 / 全部」切换显示；跨目标横向对比词云（可与 P3 合并做）。

---

### ✅ P2.5 · 语义向量化基础层（已完成 2026-08-11）

**业务目标**：为语义检索 / 聚类（"其他"治理）/ 观点去重提供基础设施——让平台具备"按语义找评论"的能力，而非仅靠关键词/标签。

**交付物（已完成）**：
- `src/analyzers/embedder.py`：本地 bge-small-zh-v1.5（512 维，零 API 成本）+ 单例加载 + `semantic_search` 语义检索
- `src/storage/db.py`：`comment_embeddings` 表（model/dim/vector BLOB）+ 仓储方法
- `src/pipeline.py`：[2.5] 入库后自动向量化（与打标解耦，`--skip-analysis` 也执行；依赖缺失自动跳过）
- `scripts/ops/backfill_embeddings.py`：增量回填（断点续跑）+ `--force` 全量重算（单事务原子切换）
- 换模型三防线：写入侧模型不一致跳过告警 / 迁移侧 `--force` 重算 / 读取侧单空间断言
- `tests/test_embedding.py`：pipeline 向量化集成测试（当前共 11 例，含 1 例 ML 环境依赖跳过）

**后续（待排期）**：
- ✅ 存量全量回填已完成（2026-08-19，1021 → 9309 条）
- 仪表盘语义搜索框（v2）
- 「其他」兜底桶治理阶段 1 已收口（2026-08-19）：扩充 30 词词典（战斗/动作/文化/反作弊/EA 等）+ 存量重匹配（`rematch_opinions.py`，兜底→具体 139 条）+ topic 兜底下沉（`recompute_topics.py`，60 条）+ 旧标签清零；观点级兜底 71.0% → **67.4%**、topic 兜底 68.6% → **67.6%**（口径：stage1_report）。剩余兜底为数据集固有（约 2/3 评论确无具体维度，纯整体褒贬），词典/prompt 均难再压，是阶段 1 后续唯一剩余杠杆（详见阻塞表）。

---

### ✅ P3 · 多目标横向对比（已完成 2026-08-19）

**业务目标**：在一个仪表盘里同时看到 10 款 Steam 游戏的舆情对比。

**示例价值**：
- 玩家视角：换游戏该看哪款？
- 行业视角：多款游戏在"主题 × 情感"上的差异化口碑格局

**📐 设计方案（已定稿并实现）**：[P3_COMPARE_DESIGN.md](./P3_COMPARE_DESIGN.md) —— 独立"多目标对比视图"，三大动作 = 同表并列 / 偏差显形 / 口径防坑；雷达图改堆叠条 + 热力图 + 散点定位图。

**✅ 已交付**：
- `app.py` 侧边栏「单目标看板 / 多目标对比」视图切换 + `render_compare()`
- 目标多选（默认 10 款）+ KPI 概览表 + 口碑散点（推荐率×情感均分×样本量）+ 情感 100% 堆叠条 + 主题×游戏热力图（占比/偏差两模式，L1/L2/L3 粒度）+ 负面痛点 TOP5 + 下钻链接
- `src/storage/db.py` 聚合方法（list_targets / opinion_matrix / sentiment_ratio / negative_pain_points）+ `src/visualizer/charts.py` 图表函数
- 原型卡片页 `product/prototype/game-compare.html`（`export_game_compare.py` 数据导出 + `build_game_compare.py` 组装，含「打开分析看板 →」跳转）
- `AppTest` 双视图 0 异常验证；顺带修复单目标看板 pandas 3.0 下 `topic_distribution` 列名 bug

---

### ✅ P4 · v0.2 文档化与回采脚本（已完成）

**现状**：`scripts/ops/refresh_likes.py` 已实现（2026-08-07）并修复游标续翻 bug（2026-08-17）；`DATA_FIELDS.md` 已补充回采字段语义，`scripts/README.md` 已按用途归类各脚本并登记新增脚本。

---

### ✅ P5 · B 站视频评论接入（采集器 2026-08-13；自动化阶段 0 落地 2026-08-23）

**为什么**：调研报告首推的扩展数据源。

**业务目标**：把"游戏评测"扩展到"B 站游戏区 UP 主评测视频评论区 + 弹幕"。

**✅ 状态**：`src/collectors/bilibili.py` 已实现并实测——BV1UpwaeNESx 全链路落库（评论 1006 条），规格见 [BILIBILI_COLLECTION.md](../architecture/BILIBILI_COLLECTION.md)。剩余交付物：跨平台仪表盘对比视图。

**✅ 采集策略已定稿（2026-08-13）**：接口实测（probe_bilibili.py，view/reply/dm/tag 均 code=0）、数据模型映射（comments/danmaku/targets）、采样策略（7 天稳态快照 / 阈值 T=2,000 全量或 K=1,000 抽样 / 弹幕时间轴分片 ≤3,000 / 默认单次采集）已全部写入规格文档：
> 📄 [architecture/BILIBILI_COLLECTION.md](../architecture/BILIBILI_COLLECTION.md)（开发窗口按此执行，含字段级映射与验收标准）

**✅ 自动化阶段 0 落地（2026-08-23）**：工程师手输 BV 号到 `bilibili_queue` 表 → 系统识别投稿时间 + 计算第 7 天 → 每日 cron 扫今天到期的视频触发采集。
> 📄 [architecture/BILIBILI_AUTOMATION.md](../architecture/BILIBILI_AUTOMATION.md)（含状态机 / CLI / cron 行为 / 失败重试 / 未来可视化扩展）

**交付物**：
- 接入 B 站公开 Web 接口（非开放平台，免申请；风控参数见规格文档第二节）
- 新增 `src/collectors/bilibili.py`（基于 probe_bilibili.py 骨架 + 阈值分支 + 弹幕分片）
- 跨平台仪表盘，能在同一个图里看"Steam 评测 vs B 站弹幕" 的情感差异
- `src/queue/{cli,runner,__main__}.py` B 站采集队列 CLI（add / list / due / run-due / skip / remove / show）
- `.github/workflows/bilibili-daily.yml` 每日 cron（UTC 17:30 北京次日凌晨 1:30，错开 Steam daily 30 分钟）
- `tests/test_bilibili_queue.py` 6 例回归测试（表 / 状态机 / 查询 / 约束 / 序列化）

**风险点**：风控参数演进（WBI/bili_ticket 盐值会更新）；超热门视频需登录 cookie；频率必须克制（规格文档 4.5 节）。

**未来扩展**（v2）：
- 关键词 / UP 主白名单自动入候选池（spec 已留 hook）
- 前端可视化（「系统管理」界面读 `bilibili_queue` 表）
- high-value 重采标记（`revisit` 字段已预留）

---

### ✅ P6 · 自动化流水线（代码完成 2026-08-20；运行态失效发现 2026-08-22；运行态收口 2026-08-23）

**现状（代码层，2026-09-11 修正）**：云端 workflow **已删除**（原 `daily-collect.yml` 是「cron + setup + Python 入口 + artifact fallback」的薄编排，`collect` job 早在 2026-09-02 就置了 `if: false`）。采集改由**本机计划任务** `VOC-Local-Daily-Collect`（北京 02:00）调用 `scripts/ops/daily_incremental_collect.py`，目标**优先读 `collect_tasks` 表**（空表回落 `config/monitoring/targets.yaml`，2026-08-25 起 `count: null` auto 模式，按时间窗耗尽替代原「每款 30 条/天，共 180 条/天」硬上限）增量采集，链末尾推库给 VPS。仓库仅剩 `.github/workflows/ci.yml`（pytest 门禁）。架构文档：[AUTOMATION_PIPELINE.md](../architecture/AUTOMATION_PIPELINE.md)。

**现状（运行态 — 2026-08-23 实测）**：
- `voc-daily-bootstrap` release 已建立（id 375081991，含本地 76 MB / 11333 条评论 DB 作为 baseline asset）
- workflow cron 每天 00:00 UTC（09:15 北京）成功触发；`conclusion: success` ✅
- artifact `voc-db-N` 上传成功（30 天 fallback）
- GH Release `voc-daily-YYYY-MM-DD` 现在可正常累积（bootstrap 已就位）
- 历史小问题：P6 从 2026-08-19 开工起 `gh_release_upload` 对「已存在 release + draft → publish」副作用不稳定 → 2026-08-22 已写入 .workbuddy/memory/2026-08-22.md A1 节，最小修复 patch 待下次小修（先 view 再 create）

**已交付**：
- 跨 run DB 持久化：每天从 GitHub Release 拉前一日 DB → 增量采集 → 上传今日 DB；空库起步回退 `voc-daily-bootstrap` 基线（已建，2026-08-23）
- 增量语义：`posted_after = max(posted_at) - 1 天` 滑窗 + 复用既有 `bulk_upsert` 去重 + `analyzed_at IS NOT NULL` 跳过 + `find_missing_embedding_ids` 增量向量化
- 失败容错：单 target 失败 try/except 不阻塞后续；release 上传失败仅记 warning
- 6 个回归测试全绿（空库起步 / 时间窗 / 不擦旧数据 / 单失败容错 / gh CLI 容错 / 时间窗边界）
- CI pytest 护栏（P10）：workflow 加 `test` job（与 `collect` 并列）；A3 推送后已上线

**剩余（脚本层小修）**：
- ✅ ~~`gh_release_upload` 副作用（先 `gh release view` 再 create）~~：已落盘（`daily_incremental_collect.py` 内 `_release_exists()` + 上传前 ensure release；同时去掉新版 gh 不支持的 `--name` flag）；workflow 端 `verify_release_upload.py` 步骤已上线——**2026-09-10 文档核对确认**（该链路现已随 `collect` job `if: false` 休眠，恢复云端采集时自动生效）

---

### ⏸️ P7 · 微博 / 小红书

- 微博：调研推荐是次选，门槛比 B 站更高（OAuth）
- 小红书：合规程度低，调研报告建议**学习用可，生产慎用**，暂缓

---

### ✅ P8 · 时间序列趋势图（2026-09-02 · Web 看板一并交付）

- **业务目标**：按日看评论量与情感构成，回答「口碑随时间怎么变」
- **✅ 已交付**：`GET /api/trends?target=&days=`（`posted_at` 日级 GROUP BY + 情感构成）+ Web 看板「时间序列」页（近 14/30/90 天，折线总量 + 情感堆叠条 + 日明细表，全部目标或单目标）
- **前置**：P6 持久化（bootstrap + 每日累积 DB）2026-08-23 已解锁；本项在 WEB_DASHBOARD 项目（阶段 4）内完成
- **实现位置**：`src/api/service.py::trends_payload` + `product/web/src/pages/trends.js`；详见 `docs/architecture/WEB_DASHBOARD.md §4.1`

---

### ⏸️ P9 · 下一代标签系统（GDT+PEDM 双轨）分阶段采纳（2026-08-15 评审通过）

**背景**：外部设计稿《下一代 AI 游戏洞察系统》（轨道 A GDT 监控分类 + 轨道 B PEDM 体验诊断 + L3.5 微话题下钻）完成评审。结论：**方法论方向采纳、不做整体替换**，改为三阶段嫁接式采纳；完整评审结论、修订版词表/接口/Prompt 与验收标准见 📄 [next-gen-tagging/ANNOTATION_SYSTEM_UPGRADE_PLAN.md](./next-gen-tagging/ANNOTATION_SYSTEM_UPGRADE_PLAN.md)（原稿备份：[next-gen-tagging/ORIGINAL_SPEC_v1.0.0.md](./next-gen-tagging/ORIGINAL_SPEC_v1.0.0.md)）。

**执行顺序（前置依赖严格）**：
1. 阶段 0 时序持久化（依赖 P6；当前每日全新库不累积，监控形态全部无从谈起）
2. 阶段 1 GDT v3.1.1 词表嫁接（**已收口，2026-08-19**：词典扩充 30 词 + 存量重匹配 + topic 兜底下沉 + 旧标签清零；观点级兜底 71.0%→67.4%、topic 兜底 68.6%→67.6%；≤30% 目标未达成——剩余兜底为数据集固有纯整体褒贬，词典/prompt 难再压，需转向数据侧）
3. 阶段 2 L3.5 本地 embedding 聚类（零 API 成本，手动触发；`l35_cluster.py` 骨架已就绪）
4. 阶段 3 PEDM 负向观点试点（黄金集一致率 ≥80% 才放量）

**暂缓**：Spike 监控大盘 / 跨版本聚合（数据量 ≥5 万条且稳定日增后重评）。

---

### ✅ P10 · 分析结果溯源 + CI pytest 工程护栏（2026-08-21）

**业务目标**：换模型 / 换 prompt 后，存量数据可按版本分组识别 + 重打或比对；CI 跑全套 pytest 防回归。

**✅ 已交付**：
- `comments.analyzer_version` 字段（`String(64), nullable=True, indexed`）；`init_db()` 内置轻量 schema 演进（nullable 列自动 ALTER，老库启动时自动加列）
- `analyzer.analyzer_version` 属性：LLM = `llm:{model}@{prompt_hash8}`；本地 = `local:{model_name}@local`
- `compute_prompt_set_hash()`：`config/prompts/*.txt` 内容 SHA256 前 8 位；任一文件改动 → hash 变 → version 变
- `pipeline.run_pipeline` 调用 `update_analysis` 时传 `analyzer_version`（无属性时传 None，向后兼容）
- `update_analysis` 接受 `analyzer_version` 入参；缺省时不擦旧值（旧 caller 安全）
- **CI pytest job**：`.github/workflows/daily-collect.yml` 加 `test` job（与 `collect` 并列），装 `requirements-core.txt`（不含 torch/transformers/sentence-transformers），`pytest tests/` 在 push/cron 都跑
- **requirements 拆分**：`requirements-core.txt`（CI/测试）/ `requirements-ml.txt`（本地 ML 可选）/ `requirements-dashboard.txt`（仪表盘）/ `requirements.txt`（本地一键全量）—— 链式 `-r` 复用
- `tests/test_analyzer_version.py` 10 例（字段存在 / 写入 / 不擦旧值 / prompt hash 稳定 / prompt 变动联动 / LLM format / local format / pipeline 传参 / 缺属性兼容 / init_db 自动 ALTER）全绿

**后续**：1）可写一个 `scripts/ops/backfill_analyzer_version.py` 给老 11332 条 NULL 数据补默认值（"legacy-pre-versioning"），方便识别；2）仪表盘可选展示"用当前 prompt 打标 vs 用旧 prompt 打标"的分布。

---

### ✅ P11 · QWEN-flash bogus 数据清理（2026-09-10 执行）

- **背景**：2026-08-24/25 三个 QWEN-flash 模型名全部 API 404，catch 块把整批静默标成 `neutral`，污染 2542 条评论（`analyzer_version=llm:qwen3-flash@55c003a3`，6 个 Steam 目标）
- **✅ 已执行**：`python scripts/ops/reset_qwen_flash_bogus.py --commit`（清理前先 WAL checkpoint + 备份 `data/backups/voc.pre-p11-20260910.db`）；2542 行 `analyzed_at` 与分析字段已重置，版本分布中 qwen 行归零、无孤儿 `comment_opinions` 残留
- **🔁 重打**：因这些评论的 `posted_at` 集中在 8/04~8/25，**已在每日 cron 的 7 天回看窗口之外**（实测近 7 天未分析 = 0），不会被自动重打 → 用 `scripts/dev/reanalyze_all.py --platform steam` 手动重打（一次性计划任务触发，避免 CLI 派生进程被冻结）
- **配套修复**：`reanalyze_all.py` 原先不写 `analyzer_version`，重打后会继续保持 NULL（与 legacy 未溯源数据无法区分）；已在 `update_analysis` 调用补 `analyzer_version=analyzer.analyzer_version`（2026-09-10）

---

### ✅ P12 · 原声分析 Agent（2026-09-08 ~ 09-10，计划外新增）

> 详见 [ORIGINAL_VOICE_ANALYSIS_AGENT.md](../architecture/ORIGINAL_VOICE_ANALYSIS_AGENT.md)（设计蓝本 + 22 项决策 + 现役修正）。

- **业务目标**：让看板从"人翻图表"升级为"用人话问数据"——平台门面级能力
- **✅ 已交付**：`src/agent/`（SSE 流式 + function calling + 4 tool + skill 系统）+ `config/agent/`（tools.yaml 抽离，prompt/描述不写死代码）+ 前端悬浮球两段式抽屉 + 「引用当前查询」（当前页筛选摘要注入上下文）+ 30 天滚动裁剪 + Markdown 导出 + 匿名 UUID 会话隔离
- **加固**：上线后对抗式审查修 P0×2（chat 端点越权 fail-open / 伪造 XFF 绕过限流）+ P1×2（LLM 配置失败无 error 事件 / list_sessions total 失真）

---

### ✅ P13 · 公网部署方案 ③（静态快照，2026-09-07）

> 选型见 [DEPLOYMENT_OPTIONS.md](../architecture/DEPLOYMENT_OPTIONS.md)，操作手册见 [STATIC_SNAPSHOT_DEPLOYMENT.md](../architecture/STATIC_SNAPSHOT_DEPLOYMENT.md)。

- **✅ 已落地**：三页预聚合 JSON 导出（383 路由 / 5.4 MB）+ 静态 shim + EdgeOne Pages 发布链路；公网 live 实测通过
- **范围**：只导三看板页（不含数据管理 / 系统管理，无写操作）；首页 = 游戏对比（静态版）
- **✅ 自动发布（2026-09-10）**：新增计划任务 `VOC-Local-Publish-Snapshot`（04:30，排在采集 02:00 / 哨兵 03:00 / Agent 裁剪 03:30 之后）→ 导出 + 发布 EdgeOne Pages；**独立任务而非并进采集**，保证发布失败不影响采集退出码与哨兵判定。⚠️ 早期文档称 daily 有 `--publish-snapshot`，实测该参数从未落地，已按独立任务方案修正文档
- **✅ ①b 内测上线（2026-09-11）**：方案 ① 定为**变体 ①b**（本机采集 + 推 DB + VPS 只读服务，含实时查询与 Agent 对话）；VPS `voc-web.service`（uvicorn `127.0.0.1:8000`）+ Caddy `:8443` 反代，公网 `http://134.175.115.248:8443` 可访问；DB 同步链路 `scripts/ops/push_db_to_vps.ps1`（`VACUUM INTO` 快照 → scp → 远端**原位** `.backup()`）已并入 02:00 采集（`--push-db`，失败只告警），实测推送后远端 `comments=18916` 与本地一致；落地清单见 [SELF_HOSTED_VPS_DEPLOYMENT.md §11](../architecture/SELF_HOSTED_VPS_DEPLOYMENT.md)。**剩余外部依赖**：域名 `erself.site` ICP 备案通过后切 HTTPS（§7B）

---
## ⚖️ 五、决策建议（业务视角）

### ⭐ 当前主线（P6 运行态失效待收口 2026-08-22；2026-09-01 更新 workflow yaml 与版本记录脱节收口）

1. ✅ ~~P6 release asset 收口~~：已于 2026-08-23 完成（bootstrap release + 9 commit 推送）
2. ✅ ~~`gh_release_upload` 副作用修复~~：2026-09-01 收口（verify_release_upload.py 步骤真正上线 daily-collect.yml）
3. ✅ ~~CI 补 pytest + analyzer_version 溯源~~：已于 2026-08-21 落地（详见 P10）；A3 推送后 CI 已真正启用
4. ✅ ~~**P8 时间序列趋势图**~~：已于 2026-09-02 在 Web 看板一并交付（`/api/trends` + 时间序列页）
5. **✅ Web 实时看板（WEB_DASHBOARD，2026-09-02 阶段 1-5 完成 + 2026-09-07 收尾）**：`src/api/` FastAPI（公开只读端点 + 管理员鉴权 + 任务 CRUD）+ `product/web/` 原生 SPA（主看板/游戏对比/B站视频/数据管理/系统管理 5 页）+ `collect_tasks` 表（Steam 目标迁入 DB）+ SQLite WAL + `bilibili_queue` paused 状态；Streamlit 并存不动。阶段 5 收口已完成（VPS 文档 + 登记四件套 + AGENTS.md 版本记录）；对抗审查 P1×4 + P2×3 已修（resume 守卫 / lifespan 无导入副作用 / 异步外部调用 / fail-closed secret / API no-store / backfill 可观测 / 前端容错 / 登录限流）。✅ **前端融合已完成（2026-09-03~05）**：三看板按用户线框图重写 + 系统管理子模块化。详见 [architecture/WEB_DASHBOARD.md](../architecture/WEB_DASHBOARD.md)
6. **✅ 本地直采 + 补采哨兵（2026-09-07）**：`VOC-Local-Daily-Collect` 02:00 直写 voc.db，`VOC-Local-Daily-Collect-Check` 03:00 检查失败/未跑并补采；GH Actions 采集 workflow **已于 2026-09-11 整体删除**（仓库仅留 `ci.yml` CI 护栏）
6. **P9 阶段 2 L3.5 微话题聚类**：`l35_cluster.py` 骨架已就绪；P6 解锁时序后，对新评论可周期性下钻
7. ~~**CS2（appid 730）补采复查**~~：已于 2026-08-23 关闭（CS2 与其他 3 款网游归档到 `data/archive/online_games_2026-08-23.db`）
8. **🆕 B 站自动化（阶段 0）**：2026-08-23 落地 — `bilibili_queue` 表 + CLI（`python -m src.queue ...`）+ workflow `bilibili-daily.yml`；工程师手输 BV 号入清单，系统识别投稿时间后自动计算第 7 天，每日 cron 触发采集；Web 看板「系统管理」已可网页增删改（2026-09-02）；详见 [architecture/BILIBILI_AUTOMATION.md](../architecture/BILIBILI_AUTOMATION.md)

### 🚦 中期主线（公网部署 ①b + L3.5 + PEDM + 作品化）

1. ✅ ~~**P8 时间序列趋势图**~~：2026-09-02 已随 Web 看板交付
2. ✅ **Web 实时看板阶段 5 收口**：VPS 部署文档更新（鉴权/WAL/8000 端口 + 03:00 补采哨兵）+ AGENTS.md 版本记录 + §6 健康检查 8 条（2026-09-07 洁癖收口）
3. ✅ ~~**P11 qwen-flash bogus 清理**~~：**2026-09-10 已执行**（实际 **2542 行**，6 个 Steam 目标；原「261 行」估算偏低）；这些评论 `posted_at` 在 8/04~8/25，**在每日 cron 的 7 天回看窗之外**（实测近 7 天未分析 = 0），故改用 `reanalyze_all.py --platform steam` 手动重打 + 补写 `analyzer_version` 溯源 —— 详见 §四 P11
4. ✅ ~~**workflow cron change + verify step push**~~：已推送（2026-09-01）；2026-09-02 起 workflow `collect` job 置 `if: false` 停用（数据链路切**本地直采**：Task Scheduler `VOC-Local-Daily-Collect` 北京 02:00 直写 voc.db；`test` job 保留作 CI 门禁）——详见 [AUTOMATION_PIPELINE.md](../architecture/AUTOMATION_PIPELINE.md) §0
5. **🆕 公网部署（①b 内测上线）**：[DEPLOYMENT_OPTIONS.md](../architecture/DEPLOYMENT_OPTIONS.md) 选型「③ 静态快照 + ① VPS」两步走——**③ 已于 2026-09-07 上线**（EdgeOne Pages，三看板快照；**2026-09-10 起由计划任务 `VOC-Local-Publish-Snapshot` 每日 04:30 自动导出 + 发布，不再需要手动**）；**① 采用变体 ①b 并已于 2026-09-11 内测上线**（腾讯云轻量·广州 `134.175.115.248`，`voc-web.service` + Caddy `:8443`；本机采集 + DB 每日随 02:00 采集推库 + VPS 只读服务，含实时查询与 Agent 对话；Agent 公开+限流小范围内测）。落地清单与实测见 [SELF_HOSTED_VPS_DEPLOYMENT.md §11](../architecture/SELF_HOSTED_VPS_DEPLOYMENT.md)；**剩余外部依赖**：域名 `erself.site` ICP 备案通过后切 HTTPS（§7B）
6. ✅ ~~**原声分析 Agent**~~：2026-09-08 ~ 09-10 落地（见 §四 P12）+ 对抗式审查加固；「引用当前查询」已接入 dashboard / bilibili / compare 三看板
7. P9 阶段 2 L3.5 微话题聚类（`l35_cluster.py` 骨架已就绪）
8. P9 阶段 3 PEDM 负向观点试点（黄金集一致率 ≥80% 才放量）

### 🌅 长期作品化（v1.0）

- 写完整文档（README 进阶版）+ 配 mermaid 架构图
- 写 1-2 篇复盘博客（踩坑 + 经验）
- 配演示视频/GIF
- 这时已可作为**求职作品集重点项目**

---

## 🚧 六、当前已知阻塞 / 待办

| 阻塞项 | 解决方式 | 阻塞了谁 |
|---|---|---|
| 🔴 **voc.db 全量重打（方案4）** | ~~跑 `reanalyze_all.py` 全量~~ ✅ **已完成（2026-08-06，2067/2067 已标注）** | ~~全部仪表盘迭代~~ |
| ~~GitHub 远端 workflow 缺失~~ | ✅ 已解决（远端 `81fb045` 已含 workflow 文件）。下次**推送 workflow 变更**时 PAT 需带 `workflow` scope（或网页端编辑） | P6 自动化任务 |
| Steam Web API key 未申请到 | 安装 Steam 手机 App → 启用 Steam Guard → 1 分钟搞定，**不阻塞任何主线** | 仅需要更大数据量时 |
| ~~B 站开放平台未申请~~ | ✅ 无需开放平台：B 站采集走公开 Web 接口（buvid + 热门视频 SESSDATA），已实测落库 | ~~P5 B 站接入~~ |
| ~~`scripts/ops/refresh_likes.py` 未实现~~ | ✅ **已实现（2026-08-07）**：回采 ≥7 天评论的 likes/replies/开发者回复 | P4 文档化收尾 |
| 🟡 "总体体验评价"兜底承载仍偏重（2026-08-19：观点级 67.4%、topic 67.6%） | ✅ 词典扩充 30 词 + 存量重匹配 + topic 兜底下沉 + 旧标签清零均已完成；prompt 增强（具体维度优先）已尝试，200 条抽样无显著提升（噪声内）——剩余兜底为数据集固有（约 2/3 评论确无具体维度可归），词典/prompt 难再压；若要继续，转向数据侧（采更多含具体维度的长评） | 仪表盘洞察力（核心价值） |
| ~~🟡 CS2（appid 730）补采 0 新增（2026-08-16 补采后仍 459 条 / 最新 08-04）~~ | ✅ **已归档（2026-08-23）**：CS2 数据 459 条已从主库移到 `data/archive/online_games_2026-08-23.db`；目标已从 `config/monitoring/targets.yaml` 显式排除；本阻塞项关闭 | — |
| ~~🔴 refresh_likes.py 翻页 bug（2026-08-15 评审发现）~~ | ✅ **已修复（2026-08-17）**：循环内 collect() 改为单次游标续翻遍历 `fetch_comments` 生成器 | Steam likes 回采现已真正生效 |
| ~~🟠 时区混用（2026-08-15 评审发现）~~ | ✅ **已统一（2026-08-17）**：posted_at 统一落库为 naive UTC（fromtimestamp 加 tz=UTC 后去 tzinfo） | "≥7天"判断与 CI 主机口径一致 |
| ~~🟠 打标双主链路分叉（2026-08-15 评审发现）~~ | ✅ **已收口（2026-08-06 方案4）**：批量+三轮收敛进 `reanalyze_all.py`；单条仅用于新评论入 pipeline | 成本与可维护性 |
| ~~🟡 CI 无 pytest~~ | ✅ **已解决（2026-08-21）**：`.github/workflows/daily-collect.yml` 加 `test` job（装 `requirements-core.txt` 不装 torch），与 `collect` job 并列；`pytest tests/` 在 push/cron 都会跑 | 工程护栏 |
| ~~🟡 分析结果无版本溯源~~ | ✅ **已解决（2026-08-21）**：`comments.analyzer_version` 字段（`{provider}:{model}@{prompt_hash8}`），LLM 与本地 analyzer 都有 `analyzer_version` 属性；prompt 文件改动自动联动 hash；老数据列已加好（值 = NULL = 未溯源） | 换模型/prompt 后存量数据可按 version 分组重打或比对 |
| ~~🟡 P11 · QWEN-flash bogus 假数据（2542 条）~~ | ✅ **已清理（2026-09-10）**：`ops/reset_qwen_flash_bogus.py --commit` 重置 2542 条 → 因 posted_at 在 cron 7 天窗之外，改用 `dev/reanalyze_all.py --platform steam` 手动重打；同步给 `reanalyze_all` 补写 `analyzer_version`（否则重打结果仍为 NULL）——详见 §四 P11 | 情感统计真实性 / 观点库 |
| 🔴 **P6 release asset 实际未上传（2026-08-22 发现）** | ✅ **已收口 2026-08-23**：`voc-daily-bootstrap` release 已建立（id 375081991，含 76 MB DB baseline），自助脚本 `pwsh scripts/dev/archive/P6_bootstrap/setup_p6_bootstrap.ps1 -Step Bootstrap` 已成功跑完 | P8 时间序列 / P9 阶段 0 / 「累积 DB」核心目标 |
| 🟡 **P6 workflow 文件推送被 PAT scope 阻塞（2026-08-22 起 8 commit 未推）** | ✅ **已收口 2026-08-23**：9 commit 已通过 GitHub Git Database API 推送（含 workflow 改动，PAT 含 `workflow` scope）；远端 main HEAD = `048db18`，workflow 含 `test:` job | CI test job 上线 |
| 🟡 下一代标签系统（GDT+PEDM 双轨） | 分阶段采纳，见 [next-gen-tagging/ANNOTATION_SYSTEM_UPGRADE_PLAN.md](./next-gen-tagging/ANNOTATION_SYSTEM_UPGRADE_PLAN.md)；阶段 1 词表已落地，语义匹配已证伪，黄金集门禁已上线 | 阶段 0 依赖 P6 持久化；阶段 1 待全量重打收口 |
| 标注算法已切换方案4 | 见 [ANNOTATION_PIPELINE.md](../architecture/ANNOTATION_PIPELINE.md) | 文档已更新 |
| 🟢 **LLM 标注降本④项（2026-09-08 立项）**——前置已完成：批量调用（10 条/批）、`reasoning_effort=low`、前缀缓存友好、`update_analysis` flush 修复（见 AGENTS.md 2026-09-08 行） | ① **Batch API 半价**：GLM/通义均支持异步批量（输入输出半价），标注是离线场景完美匹配——把分析阶段改为「积攒待标 → 异步提交 → 次日取结果」或独立批量任务；② **内容去重缓存**：短评重复率高（"好评""111"），按 content hash 复用标注结果（comments 加 hash 列或独立缓存表，analyze 前查重）；③ **短文本走本地模型**：极短/无具体维度评论（约占 2/3）走 `sentiment_local`（零边际成本），LLM 只标长评/高赞——需先用 golden set 评测 local 准确率；④ **Qwen3-8B/14B 评测**：SiliconFlow/百炼免费额度跑 golden set 对比 GLM-5.3-Flash（0.8/2.8 元每百万），达标则成本降至 1/5~1/10——⚠️ 用百炼正式端点，勿用 token-plan 个人版（8/25 模型名 404 事故） | 标注成本（8 游戏 × 每日数百条 + B站千条级首采，LLM 调用为最大可变成本） |
| 🟠 **Steam 代理断网 → 02:00 采集与 03:00 哨兵同时失效（2026-09-07 发现）** | 本机依赖代理 `127.0.0.1:7877`（Python 不走系统代理）；可选根治：`SteamCollector` 直连失败回落 `STEAM_PROXY` env。**已知局限**：哨兵与主任务仅隔 1h，整夜断网时补采同样失败 | 每日数据完整性（观察中） |
| 🟠 **本机 Web 服务无守护进程** | uvicorn 靠手动重启（且必须用 `.venv-ml` 解释器，否则 backfill 线程静默 ModuleNotFoundError）；方案 ①b 落地后由 VPS `voc-web.service` 接管 | 本地看板可用性 |
| ~~🔴 4 款网游回灌主库（2026-09-10 发现 → 当日修复）~~ | ✅ **已重新归档（2026-09-10）**：`ops/archive_online_games.py` 抽到 `data/archive/online_games_2026-09-10.db`（4,916 条 / 7,101 观点 / 4,915 向量，26.2 MB）后从主库删除 + VACUUM（144.2 → **115.7 MB**）；主库 steam 目标 13 → **8**、网游残留 **0**；操作前备份 `data/backups/voc.pre-rearchive-20260910.db`。**复发防御**：GH Release 上的 `voc-daily-*` asset 仍是归档前 DB，**不要再跑 `sync_local_from_release` / `smart_sync_release` 覆盖本地库**（如需恢复云端累积，先重建 release baseline）；已确认当前无 VOC sync 计划任务 | 数据口径（「主库聚焦 8 单机」）/ 看板默认目标 |

---

## 📈 七、复盘（重要的工作风格沉淀）

> 这些是这次实战踩过的坑，方便下次直接绕开。

### 工程化小修（已解决）
- ⚠️ **Windows 上 SQLite 文件锁**导致 pytest 死锁 → `engine.dispose()` + `try/finally` 容错
- ⚠️ **Python 3.12+ 弃用 `datetime.utcnow()`** → 用 `_utcnow()` 工具函数替代，22 个 warning 清零

### v0.2 踩坑（已沉淀到代码注释 / 文档）
- ⚠️ **Steam API `filter="all"` + `day_range` 隐藏 bug**：
  - `filter="recent"` + `day_range=N` → **不生效**，返回游戏全量评论按时间排序
  - `filter="all"` + `day_range=N` → 仅返回最近 N 天内 Steam 标记的"helpful"评论
  - **不能依赖 Steam API 自身的时间窗过滤**，必须用应用层 `posted_after / posted_before` 实现
    - （2026-08-15 评审注：day_range 的生效条件实际从未受控验证，且项目内两处历史记载相互矛盾；"不依赖 Steam 时间窗、恒传 0 + 应用层过滤"的结论仍然成立，详见 `src/collectors/steam.py` 注释）
- ⚠️ **Steam API 翻页 bug**：跨页时偶尔返回已见过的 `recommendationid`，必须用 `seen_source_ids` 去重
- ⚠️ **冷启动 likes=0 误导**：评论刚发布时点赞数=0，与"无人认可"语义不同，必须用 `NULL=未回采` 区分

### v0.7 踩坑（已沉淀到 MEMORY.md）
- ⚠️ **QWEN-flash 个人 token-plan 模型名 ≠ 阿里通用文档**：8/24-25 试 `qwen3.7-flash` / `qwen3-flash` / `qwen3.6-flash` 全部 API 404；2542 条 bogus 数据污染 DB，P11 cleanup 工具就位
- ⚠️ **GH Actions `on: schedule` 没 SLA**：8/22-25 + 8/27 三次 release assets=[]（silent 失败），UI 仍显示 success；防御：`scripts/ops/verify_release_upload.py` + workflow step `if: always()`

### Vibe Coding 经验
- ✅ **从「最快见到反馈」开始**：先跑通 Steam 一个游戏 50 条，比一开始就堆多平台更关键
- ✅ **业务语言优先**：仪表盘给人"看出什么"，比 dashboard 用了什么图表库更重要
- ✅ **保留可关闭的复杂度**：BERT 本地分析是亮点，但默认走 DeepSeek，这样 95% 用户不会被冷启动劝退
- ✅ **配置外部化效果立现**：v0.2 主题精细化只需改 `config/topics/gaming.yaml`，不必改 Python
- ✅ **批量采集时不必每次都分析**：先采集、后分析是分离两阶段，pipeline 跑两次也才几分钟

### 安全
- 🔒 DeepSeek Key **未经过 stdout / log**（直接 PowerShell 进程内字符串替换 `.env`）
- 🔒 `.env` 在 `.gitignore` 内，不会被提交
- 🔒 `data/voc.db` 与 `*.bak` 文件在 `.gitignore` 内，运行时不入库

---

## 📅 八、各里程碑业务指标

| 里程碑 | 业务里程碑判据 | 状态 |
|---|---|---|
| M1（初始化） | 仓库可被克隆 + 5 分钟跑起来 | ✅ |
| M2（调研） | 一份合格的竞品调研报告 | ✅ |
| M3（仓库） | GitHub 公开可达，MIT license | ✅ |
| M4（基础版） | smoke test + pytest 全绿 | ✅ |
| M5（v0.1） | 完整链路跑通，仪表盘可演示（50 条 CS2；CS2 后续已归档） | ✅ |
| M6 · v0.2 | 数据采集生命周期管理 + 6 款游戏批量采集 + 7 天后回采机制 | ✅ |
| M7 · v0.3 | 主题分类精细（L1-L3 三级标签）+ 词云 + 语义向量化（P2.5），仪表盘有"洞察力" | ✅ |
| M8 · v0.4 | 多目标横向对比（10 款 Steam 同看 → 2026-08-23 后主库 6 款单机 + 4 款归档可单独查） | ✅（2026-08-19：Streamlit 对比视图 + 原型卡片页 + 下钻；2026-08-23 归档后多目标视图仍可显示所有 target，只需切到归档 DB） |
| M9 · v0.5 | 多平台覆盖（Steam + B 站） | ✅ |
| M10 · v0.6 | 自动化每日采集 + 时间序列趋势 | ✅（代码完成 2026-08-20；运行态收口 2026-08-23：bootstrap release 已建 + 9 commit 推送 + CI test job 上线）；`gh_release_upload` 副作用 patch 已落盘（2026-09-10 核对：`_release_exists()` + 去 `--name` flag + workflow verify step） |
| M11 · v0.7 | 分析结果溯源 + CI pytest 护栏 | ✅（2026-08-21：P10 analyzer_version 字段 + init_db 自动演进 + CI test job）；2026-09-01 加 DESIGN_TOKENS v1.0（三原型 v2 迁移版上线 + tokens.css 单一来源） |
| M12 · v0.8 | Web 实时看板 + 静态快照部署 + 原声分析 Agent | ✅（2026-09-02 ~ 09-10：SPA 5 页 + `collect_tasks` + WAL；方案 ③ EdgeOne Pages 上线；Agent 悬浮球抽屉 + 4 tool + 对抗审查加固；测试 49 → 219 例） |
| M13 · v1.0 | 完整文档 + 复盘博客 + 简历亮点包 | P8 时间序列 / P11 清理已于 2026-09-10 完成，前置全部解锁；剩 P9 阶段 2/3 + 作品化 4 件套 |

**M8 → M13 路径**：
1. M8：✅ P3 多目标对比已完成（2026-08-19）
2. M9：✅ B 站采集器已落地（跨平台对比视图为可选增强，归 P5）
3. M10：✅ P6 自动化流水线已收口（2026-08-23：bootstrap release + 9 commit 推送 + CI test job 上线）；✅ P8 时间序列趋势图已于 2026-09-02 随 Web 看板交付（前置全部解锁）
4. M11：✅ P10 分析溯源 + CI pytest 已完成（2026-08-21）
5. M12：✅ Web 实时看板 + 方案 ③ 静态快照 + 原声分析 Agent 已于 2026-09-02 ~ 09-10 交付；✅ P11 bogus 清理 2026-09-10 执行
6. M13：⏳ 待推进 —— P9 阶段 2/3 + 作品化 4 件套（完整文档 / mermaid 架构图 / 复盘博客 / 演示视频）

---

## 📝 九、何时该重新审视这份计划

建议在以下情况暂停、回到这份文档对齐：
- 上线某里程碑后发现与目标有偏差
- 调研报告里的优先级排序已不适用（如 B 站接入遇到新问题）
- 个人时间精力有较大变化（暂停 / 重启）
- v0.2 后续要不要扩展到多平台：B 站/微博/京东的优先级是否调整

---

> 💡 **记住**：本项目核心价值 = 边学边做 + 能看到玩家真实声音。**不要为了完整功能而忘了这个核心价值**。
