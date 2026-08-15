# VoC 平台 · 开发计划与进度速查

> **用途**：项目从 0 到 1 全部里程碑 + 下一步候选 + 当前状态快照。随时查阅。
>
> **关联文档**：
> - 项目说明：[README.md](../../README.md)
> - 5 分钟上手：[QUICK_START.md](../guides/QUICK_START.md)
> - 调研报告：[VoC平台竞品调研报告.md](../research/VoC平台竞品调研报告.md)
> - 字段字典：[DATA_FIELDS.md](../architecture/DATA_FIELDS.md)
> - 存储设计：[DATA_STORAGE_DESIGN.md](../architecture/DATA_STORAGE_DESIGN.md)
> - Steam API 字段：[STEAM_API_FIELDS.md](../STEAM_API_FIELDS.md)
>
> **最后更新**：2026-08-15

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

> 全部完成项都经验证可在本地一键复现。

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
- `refresh_likes.py`：发布满 7 天的评论回采点赞/回复/开发者回复（**未实现，待补**）
- `collect_6_games.py`：6 款 Steam 游戏批量采集（黑神话悟空 / 巫师3 / 文明6 / 底特律 / 光与影 33号远征队 / 星际拓荒）
- `check_likes_status.py`：检查评论 likes 状态分布
- `cleanup_cs2.py` / `inspect_aug3_data.py`：数据清理与巡检
- `debug_dup_source_id.py` / `debug_pagination_loss.py` / `debug_recent_order.py`：翻页 bug 排查
- `verify_appids.py` / `verify_appids_zh.py` / `verify_collect.py`：appid 与采集验证
- `e2e_lifecycle.py`：首次采集 + 回采全链路 E2E 测试
- `show_refreshed_sample.py`：回采样本展示

**新增文档**：
- `docs/STEAM_API_FIELDS.md`：Steam API 字段大全（设计师对接 + 字段字典）

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

## 📊 三、当前数据快照（2026-08-15）

| 指标 | 数值 | 业务解读 |
|---|---|---|
| 总评论数 | **3073 条** | Steam 2067（6 款游戏）+ B站 1006（BV1UpwaeNESx 实测落库） |
| 情感分析覆盖 | **3034/3073（98.7%）** | ✅ 方案4 打标（Steam 重打 + B站 新入库即打标）；余 39 条待分析 |
| 观点级标注 | 5212 条 | `comment_opinions` 表（程序匹配到的观点短语） |
| 语义向量覆盖 | 1021 条（单模型） | `comment_embeddings` 表（bge-small-zh-v1.5；Steam 存量 2067 待回填） |
| 已支持游戏/视频 | 6 款游戏 + 1 条视频 | Steam 6 款 + B站 BV1UpwaeNESx |
| 正向 / 负向 / 中性 | 见 DB | 按核心观点情感聚合 |
| 主题 TOP1 | 见 DB | L1-L3 三级标签（L1 7 / L2 28 / L3 128） |
| 部署方式 | 本地 Streamlit | 在内网/笔记本即可跑 |
| 平台覆盖 | **Steam + B站（2 家）** | 微博为下一主扩展点 |
| 数据存储 | SQLite 单文件（data/voc.db） | 增量 7 天后回采机制 |
| 远端 GitHub 状态 | 推送至 commit `81fb045` | 本次 B站/向量化工作未提交 |

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

### 🥇 P1 · 主题分类精细化（1-2 小时）

**问题**：当前主题分类把所有内容堆到"游戏性"里，仪表盘看不出玩家具体在抱怨什么。

**业务目标**：让仪表盘的"主题 TOP10"一眼看出玩家具体讨论：
> 画质 / 手感 / 平衡性 / 竞技公平 / 帧数 / 价格 / 外挂 / 匹配 / 服务器 / 客服 / 音效 / ...

**交付物**：
- 优化 `config/topics/gaming.yaml`，主题词表从 10 个扩到 12-18 个细颗粒度标签
- 同步调整 `config/prompts/sentiment_user.txt` 的范例
- 6 款游戏 772 条重新跑分析，主题分布呈现 6-8 个细分
- 仪表盘"主题分布"图立刻直观

**为什么排第一**：
- 见效最直接，立竿见影提升仪表盘说服力
- 修改只需改 `config/` + 一次重跑（已外置配置，价值立刻体现）
- 无外部依赖（DeepSeek 已有 key）
- 是面试/演示时**最容易被记住的亮点**

---

### 🥇 P2 · 仪表盘加入"评论词云"（30 分钟）

**问题**：仪表盘还没显示"玩家嘴里最常说的词"。

**业务目标**：当用户打开仪表盘，词云直接告诉他"外挂、卡顿、退款、稀有"是热点词。

**交付物**：
- 在仪表盘加一个标签页 / Tab：词云图
- 用 jieba 做中文分词 + 词云生成
- 支持按"正向/负向/全部"切换显示
- 适配 6 款游戏横向对比（每款一张词云）

**依赖**：`jieba` 与 `wordcloud` 已装（requirements.txt 内有），零额外依赖

---

### ✅ P2.5 · 语义向量化基础层（已完成 2026-08-11）

**业务目标**：为语义检索 / 聚类（"其他"治理）/ 观点去重提供基础设施——让平台具备"按语义找评论"的能力，而非仅靠关键词/标签。

**交付物（已完成）**：
- `src/analyzers/embedder.py`：本地 bge-small-zh-v1.5（512 维，零 API 成本）+ 单例加载 + `semantic_search` 语义检索
- `src/storage/db.py`：`comment_embeddings` 表（model/dim/vector BLOB）+ 仓储方法
- `src/pipeline.py`：[2.5] 入库后自动向量化（与打标解耦，`--skip-analysis` 也执行；依赖缺失自动跳过）
- `scripts/ops/backfill_embeddings.py`：增量回填（断点续跑）+ `--force` 全量重算（单事务原子切换）
- 换模型三防线：写入侧模型不一致跳过告警 / 迁移侧 `--force` 重算 / 读取侧单空间断言
- `tests/test_embedding.py`：pipeline 向量化集成测试（新增后共 10 例：9 通过 + 1 环境依赖跳过）

**后续（待排期）**：
- 存量 2067 条全量回填（`backfill_embeddings.py` 一次跑完，约几分钟）
- 仪表盘语义搜索框（v2）
- 「其他」兜底桶已升至 2032 条 / 67.0%（topic 口径，2026-08-15 实测；观点口径 62.4%，趋势恶化）→ 匹配层语义匹配升级 + embedding 聚类治理（当前数据最大痛点）

---

### 🥈 P3 · 多目标横向对比（2-3 小时）

**业务目标**：在一个仪表盘里同时看到"黑神话 vs 巫师3 vs 文明6 vs CS2" 等 6 款游戏的舆情对比。

**示例价值**：
- 玩家视角：换游戏该看哪款？
- 行业视角：6 款 3A 游戏的 RPG 口碑差异

**交付物**：
- 仪表盘顶部加一个"目标游戏选择器"，支持对比 2-4 款游戏
- 每游戏一张小型雷达图（情感 / 体量 / 主题分布）
- 一张合并的"主题 × 情感" 热力图

---

### 🥈 P4 · 补全 v0.2 文档化与回采脚本（0.5-1 天）

**问题**：v0.2 的代码已 commit（`ca04681`），但 `scripts/ops/refresh_likes.py` 还没实现，文档也未沉淀。

**业务目标**：让 v0.2 的设计意图可被未来工程师快速理解。

**交付物**：
- 实现 `scripts/ops/refresh_likes.py`：扫"已发布 ≥7 天的评论" → 重新拉 Steam 评论详情 → 填回 likes/replies/developer_response
- 文档：更新 `docs/architecture/DATA_FIELDS.md`，增补 `likes_refreshed_at` / `developer_response_refreshed_at` 的语义说明
- 文档：把 `scripts/dev/` 各脚本的用途归类到 `scripts/README.md`（已有框架，缺填充）

---

### 🥉 P5 · B 站视频评论接入（采集器已完成 2026-08-13，跨平台仪表盘待做）

**为什么**：调研报告首推的扩展数据源。

**业务目标**：把"游戏评测"扩展到"B 站游戏区 UP 主评测视频评论区 + 弹幕"。

**✅ 状态**：`src/collectors/bilibili.py` 已实现并实测——BV1UpwaeNESx 全链路落库（评论 1006 条），规格见 [BILIBILI_COLLECTION.md](../architecture/BILIBILI_COLLECTION.md)。剩余交付物：跨平台仪表盘对比视图。

**✅ 采集策略已定稿（2026-08-13）**：接口实测（probe_bilibili.py，view/reply/dm/tag 均 code=0）、数据模型映射（comments/danmaku/targets）、采样策略（7 天稳态快照 / 阈值 T=2,000 全量或 K=1,000 抽样 / 弹幕时间轴分片 ≤3,000 / 默认单次采集）已全部写入规格文档：
> 📄 [architecture/BILIBILI_COLLECTION.md](../architecture/BILIBILI_COLLECTION.md)（开发窗口按此执行，含字段级映射与验收标准）

**交付物**：
- 接入 B 站公开 Web 接口（非开放平台，免申请；风控参数见规格文档第二节）
- 新增 `src/collectors/bilibili.py`（基于 probe_bilibili.py 骨架 + 阈值分支 + 弹幕分片）
- 跨平台仪表盘，能在同一个图里看"Steam 评测 vs B 站弹幕" 的情感差异

**风险点**：风控参数演进（WBI/bili_ticket 盐值会更新）；超热门视频需登录 cookie；频率必须克制（规格文档 4.5 节）。

---

### 🥉 P6 · 自动化（每日自动跑采集 + 分析）

**业务目标**：人不在电脑旁，平台也能持续积累数据。

**交付物**：
- GitHub Actions 每天凌晨 2 点触发一次采集 + 分析
- 累计数据形成"时间序列趋势"
- 解决遗留的 PAT `workflow` scope 问题（在 GitHub 网页手动创建 workflow 也行）

**前依赖**：
- ✅ 远端已含 workflow 文件（自 `81fb045` 起）。下次**修改并推送** `daily-collect.yml` 时，PAT 仍需带 `workflow` scope（或走网页端编辑），见阻塞表

---

### ⏸️ P7 · 微博 / 小红书

- 微博：调研推荐是次选，门槛比 B 站更高（OAuth）
- 小红书：合规程度低，调研报告建议**学习用可，生产慎用**，暂缓

---

### ⏸️ P8 · 时间序列趋势图（数据量到 1000+ 条再做）

- 当前 772 条接近门槛，可优先做
- 等自动采集跑 2 周累积几千条再做才有意义
- 工具：`streamlit` 自带的折线图即可

---

## ⚖️ 五、决策建议（业务视角）

### ⭐ 短期冲刺组合（P0 + P1 + P2，1 天搞定）

1. **P0 修复分析覆盖**（让仪表盘恢复活力）
2. **P1 主题精细化**（提升"分析"专业度）
3. **P2 词云图**（提升"可视化"亲和度）

> 🎯 三个加起来 1 天，立竿见影让仪表盘从「空数据」升级为「有洞察力」。
>
> 适合 Vibe Coding 节奏 —— 一个晚上就能感受到巨大提升。

### 🚦 中期主线（P3 + P4 + P5，1-2 周）

1. P3 多目标对比（让仪表盘能横向看 6 款游戏）
2. P4 v0.2 文档化（让回采机制可被未来工程师理解）
3. P5 B 站接入（数据源翻倍）

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
| B 站开放平台未申请 | 提交申请即可（个人开发者 1-3 天审核） | P5 B 站接入 |
| ~~`scripts/ops/refresh_likes.py` 未实现~~ | ✅ **已实现（2026-08-07）**：回采 ≥7 天评论的 likes/replies/开发者回复 | P4 文档化收尾 |
| 🔴 "其他/整体评价"占比恶化至 67%（2026-08-15 实测） | 匹配层升级：phrase→L3 定义语义匹配（复用 bge 向量）+ 收紧 ≤20 字兜底 + 黄金集回归门禁 | 仪表盘洞察力（核心价值） |
| 🔴 refresh_likes.py 翻页 bug（2026-08-15 评审发现） | 循环内 collect() 每次从头翻页，只覆盖最新 ~100 条且 matched 重复计数 → 改为真正游标续翻 | Steam likes 回采从未生效（全库 likes 为 NULL） |
| 🟠 时区混用（2026-08-15 评审发现） | posted_at 存本地时间（fromtimestamp），fetched_at/refreshed_at 为 UTC → 统一 tz-aware UTC | "≥7天"判断有 8h 偏差；CI（UTC 主机）与本地采集数据不可比 |
| 🟠 打标双主链路分叉（2026-08-15 评审发现） | pipeline 逐条调 LLM（约 10x 成本），批量+三轮收敛只在 reanalyze_all.py → 收口进主链路 | 成本与可维护性 |
| 🟡 CI 无 pytest | GitHub Actions 增加 test job；拆分 requirements 避免 CI 安装 torch | 工程护栏 |
| 🟡 分析结果无版本溯源 | comments 增加 analyzer_version（模型+prompt 版本） | 换模型/prompt 后存量数据无法对账 |
| 标注算法已切换方案4 | 见 [ANNOTATION_PIPELINE.md](../architecture/ANNOTATION_PIPELINE.md) | 文档已更新 |

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
| M5（v0.1） | 完整链路跑通，仪表盘可演示（50 条 CS2） | ✅ |
| **M6（v0.2 当前）** | **数据采集生命周期管理 + 6 款游戏批量采集 + 7 天后回采机制** | ✅ |
| M7 · v0.3 | 主题分类精细（L1-L3 三级标签）+ 词云 + 语义向量化（P2.5），仪表盘有"洞察力" | ✅ |
| M8 · v0.4 | 多目标横向对比（6 款游戏同看） | 待 M7 |
| M9 · v0.5 | 多平台覆盖（Steam + B 站） | 待 M8 |
| M10 · v0.6 | 自动化每日采集 + 时间序列趋势 | 待 M9 |
| M11 · v1.0 | 完整文档 + 复盘博客 + 简历亮点包 | 待 M10 |

**M6 → M7 路径**：
1. P0：771 条评论重跑分析（解锁仪表盘）
2. P1：主题词表扩到 12-18 个 + 重跑分析
3. P2：仪表盘加词云
4. M7 完成

---

## 📝 九、何时该重新审视这份计划

建议在以下情况暂停、回到这份文档对齐：
- 上线某里程碑后发现与目标有偏差
- 调研报告里的优先级排序已不适用（如 B 站接入遇到新问题）
- 个人时间精力有较大变化（暂停 / 重启）
- v0.2 后续要不要扩展到多平台：B 站/微博/京东的优先级是否调整

---

> 💡 **记住**：本项目核心价值 = 边学边做 + 能看到玩家真实声音。**不要为了完整功能而忘了这个核心价值**。
>
> 🎯 **当前最关键的一步**：治理标注匹配层（把「其他」占比从 67% 打下来）+ 修复 refresh_likes 翻页 bug——先巩固存量数据价值，再扩新平台。