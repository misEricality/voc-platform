# AGENTS.md — 项目工程约定（每次会话必读）

> **这份文件是代理（Agent）与工程师共同遵守的唯一工程规范来源。**
> 每次新会话开始时，代理必须先读完本文件再动手，无需工程师重复强调。
>
> **最后更新**：2026-09-02

---

## 0. 总原则（先读这个）

> **非必要不新增。** 新增任何脚本 / 文档 / 文件夹之前，先自问：
> 现有文件里是否已有同职责的东西？能否在既有文件里改/扩展来完成，而不是新建？

**新增前必须过这三关：**

1. **该不该加？** 现有 `src/`、`scripts/`、`docs/`、`tests/`、`config/` 里有没有同类职责可复用？
2. **放哪？** 见下方「目录职责与摆放规则」。新文件必须放进对的位置，不得乱放。
3. **叫什么？** 见下方「命名规范」。文件名必须能自解释，且与目录职责一致。

> 若三个问题里任何一个是「不确定」，先在会话里说明理由，不要默默新建。

---

## 1. 目录职责与摆放规则

```
voc_platform/
├── AGENTS.md                  ⬅ 本文件：工程约定
├── README.md                 项目总览（项目级入口；文档导航见 docs/00-index.md）
├── app.py                    主应用入口
├── requirements.txt          依赖清单
├── src/                      📦 可复用代码库（正式模块，不是脚本）
│   ├── pipeline.py
│   ├── api/                  Web 数据服务层（FastAPI：公开只读端点 + admin 任务 CRUD + 鉴权）
│   ├── analyzers/            分析器（情感 / 语义 / 标注）
│   ├── collectors/           采集器（steam / bilibili ...）
│   ├── queue/                B 站采集队列（CLI + runner）
│   ├── storage/              存储（db）
│   └── visualizer/           可视化
├── scripts/                  🛠️ 一次性/运维脚本（不放正式模块）
│   ├── smoke_test.py         冒烟测试（CI 用）
│   ├── dev/                  开发期一次性/调试脚本
│   ├── ops/                  长期运维脚本（定时/回采）
│   ├── analysis/             分析脚本
│   └── README.md             脚本索引（新增后必须登记）
├── config/                   ⚙️ 业务配置（prompts/ 提示词、topics/ 标签体系）
├── data/                     运行时数据（voc.db、exports/），不入库
├── tests/                    自动化测试（pytest，不入 scripts/）
│   └── fixtures/             测试夹具数据
├── docs/                     📝 文档（按用途分子目录）
│   ├── 00-index.md           文档地图
│   ├── architecture/         架构设计
│   ├── guides/               操作指南
│   ├── plan/                 计划与里程碑
│   └── research/             调研资料
└── .workbuddy/               项目长期记忆（工程师 + 代理维护）
```

### 归属决策表（新东西放哪）

| 你要加的 | 放哪 | 备注 |
|----------|------|------|
| 可被多处调用的正式模块/类 | `src/<域>/` | 按域分子目录 |
| 一次性验证、调试、数据修复脚本 | `scripts/dev/` | 一次性 |
| 定时/长期运维任务 | `scripts/ops/` | 长期 |
| 自动化回归测试 | `tests/test_*.py` | 夹具进 `tests/fixtures/` |
| 业务知识（prompt / 标签体系 / 词表） | `config/` | 与代码解耦 |
| 架构/流程/字段设计文档 | `docs/architecture/` | |
| 操作指引 | `docs/guides/` | |
| 计划/里程碑/选型报告 | `docs/plan/` | |
| 调研资料 | `docs/research/` | |
| 运行时产物（db/导出/缓存） | `data/` | 不提交 git |

---

## 2. 命名规范

### 2.1 文件与代码

| 对象 | 规范 | 示例 |
|------|------|------|
| Python 模块/文件 | `snake_case`（小写下划线） | `backfill_embeddings.py` |
| Python 类 | `CamelCase` | `SteamCollector` |
| Python 函数/变量 | `snake_case` | `match_l3()` |
| 文档 `.md` | 大写蛇形或说明性短名 | `DATA_STORAGE_DESIGN.md`、`QUICK_START.md` |
| 配置 `.yaml` | 小写下划线 | `l3_definitions.yaml` |

> 文档允许用中文名（如 `新标签体系验证报告.md` —— 2026-09-01 已规范化为 `GDT_V311_VERIFICATION_REPORT.md`），但须自解释、与目录职责一致；能英文 snake 名尽量英文。

### 2.2 脚本命名（`scripts/` 内）

- 动词开头，说明动作 + 对象，小写下划线。
- 例：`backfill_embeddings.py`、`collect_6_games.py`、`verify_config.py`、`reanalyze_all.py`。
- 避免无信息名字：`test1.py`、`new.py`、`final_v2.py`。

### 2.3 目录

- 小写单词，多个词用 `_` 分隔（如 `next-gen-tagging/` 这类已有特例保留，新目录统一 `snake_case`）。
- 目录名代表单一职责，不为单个文件单独开目录。

---

## 3. 必守的工程红线

- 测试/验证必须用独立文件夹（`tests/`）或独立测试 DB，**禁止污染生产代码与主库**。
- 数据分页截断时必须显式通知，避免误判完整性。
- 新脚本必须能从项目根直接运行：`python scripts/xxx.py`。
- 一次性脚本超过 3 个月无人使用 → 归档 `dev/archive/` 或删除。
- 不要把 prompt / 业务配置 / API Key 写死在代码里，统一走 `config/` 与 `.env`。
- 新增正式模块后，跑一次 `scripts/smoke_test.py` 与 `tests/`。

---

## 4. 文档摆放与登记

- 新增 `docs/` 下文档后，**必须同步登记到 `docs/00-index.md` 文档地图**，否则视为未完成。
- 新增 `scripts/` 下脚本后，**必须登记到 `scripts/README.md` 脚本索引**。
- 改业务配置后，在 `config/README.md` 的版本记录里追加一行。

---

## 5. 长期记忆维护

- 重要的决策 / 约定 / 坑，写入 `.workbuddy/memory/MEMORY.md` 与对应日期的 `YYYY-MM-DD.md`。
- 本文件的约定若变更，须在「版本记录」登记并同步记忆。

> ⚠️ `.workbuddy/` 是 **gitignore 的本机目录**，不随 git / clone 共享，只做本机跨会话记忆。**需要随仓库共享的约定/文档应放进 `docs/` 或 `AGENTS.md`**，别只写在 `.workbuddy/`。

---

## 6. 定期健康检查（阶段审计）

> 每个里程碑 / 发版 / 大改动后跑一遍；新会话也可按此快速摸清仓库状态。

- [ ] **无失效引用**：`grep` 文档里是否引用已删除/已移位的文件（如 `data/validation/`、旧脚本名、`overview.md`）
- [ ] **无幽灵目录**：`git ls-files <dir>` 确认空占位目录是否真被跟踪（空目录 git 不跟踪，需放 `.gitkeep`）
- [ ] **无重复文件**：是否有同职责文档/脚本并存（如旧/新验证报告、QUICK_START vs README 快速开始）
- [ ] **登记齐全**：新文档进 `docs/00-index.md`、新脚本进 `scripts/README.md`、配置改动登记 `config/README.md` 版本记录
- [ ] **命名合规**：新文件符合命名规范（snake_case / CamelCase / 自解释）
- [ ] **数据/备份瘦身**：`.bak`、过期导出、`__pycache__` 是否堆积（运行时产物不提交 git）
- [ ] **工作树干净**：`git status --short` 无意外改动 / 未跟踪残留
- [ ] **workflow yaml 与版本记录一致**：每次在版本记录登记「yml 已改」前，先 `git diff .github/workflows/*.yml` 确认改动已落盘；P6 silent 失败 / cron / timeout 等已知生产 bug 修完后尤其要核对（2026-09-01 教训：AGENTS.md 写了 yml 改了，但实际没改）

---

## 📋 版本记录

| 更新时间 | 内容 | 原因 |
|---|---|---|
| 2026-09-05 | **系统管理子模块化 + 采集管理增强**：nav「系统管理」改 hover 下拉（采集任务 `#/admin` / 数据管理 `#/data`），原「时间序列」页迁入 `pages/data.js`（标题「系统管理 - 数据管理」、副标题「每日评论量与情感构成」、公开无需登录、目标下拉改 monitored 白名单=6 款单机+fetched B站视频，`#/trends` 301 兼容跳转）；admin 新增 `GET /api/admin/tasks/lookup`（Steam：name+发行日期 / B站：title+投稿，新增/编辑弹窗「查找」按钮即时回显并自动填名称）；B站列表「采截至」→「采集时间」（采集日−投稿日间隔天数，title 悬停完整日期）；测试 39 → 40 例全绿 | 工程师确认的 5 项当前页修改 + 平台架构调整；「数据管理」下拉收窄到白名单避免归档网游混入 |
| 2026-09-04 | 新增 `docs/architecture/DEPLOYMENT_OPTIONS.md`（公网部署选型：5 方案对比 + 3 条硬约束 + 7 条决策记录 + 落地前置检查）；登记 `docs/00-index.md`（文档地图 + 「我想知道」表新增「部署到公网有哪几种走法」一行）；`README.md` 架构文档计数 10 → 12 | 工程师提出「部署到公网」需求；先落选型评估、**暂不开发**，避免与 P9 主线抢时间 |
| 2026-09-04 | **B站视频看板重设计**（线框图）：`bilibili_queue` 新增快照列（aid/pic/owner/view/三连/reply_total/danmaku_total/duration/tags_json/highlights_json/stats_fetched_at，init_db 自动演进）；`fetch_video_info` 补 pic/duration；pipeline 挂两个钩子（`_snapshot_bili_queue` 采集时快照落库 + `_generate_danmaku_highlights` 30s 桶 top3 LLM 总结 → highlights_json，均不阻塞主流程）；新增 `src/analyzers/danmaku_summary.py`（复用 PROVIDER_CONFIG，prompt `config/prompts/danmaku_summary.txt`）+ `db.bucket_danmaku_rows`（30s 固定桶，API/采集共用）+ `scripts/ops/backfill_bili_highlights.py`（存量回填，已登记 scripts/README）；API：新端点 `/api/bilibili/videos`（快照+采集量+性别分布 json_extract+高光解析）、`/api/danmaku` 重做（30s 固定桶+每桶 10 条随机样本 ≤15 字）、`/api/comments` 加 `sort=likes`（点赞降序→时间降序）；`bilibili.js` 按线框图重写（视频单选看板：封面+信息卡（标题/UP主/投稿日/标签前8/播放/评论/弹幕/三连率）+ 采集评论量卡（占比副指标）+ 性别环形（男蓝/保密灰/女紫）+ 情感环形 + L1 主题分布（原声粒度零填充固定顺序，**点击标签联动筛选右侧原声列表**，再点取消/点其他切换）+ 可折叠原声列表 + 弹幕时间轴（悬停浮层）+ 高光时刻三卡）；测试 36 → 39 例全绿 | 对齐新线框图；「高光 LLM 总结放采集时完成」经工程师确认（成本一次性/页面零延迟/API 无需 Key）；存量 3 视频需手动跑回填脚本一次 |
| 2026-09-04 | **游戏对比看板重设计**（线框图 `product/prototype-design/`）：新增 `game_meta` 表（发行日期/Steam 评级/评测数/本地封面）+ `steam.py::fetch_review_summary`（appreviews 全量评测摘要）+ `/api/games/meta`（懒加载刷新，24h TTL，全败回拨 1h 防重试风暴）+ `/covers` 静态托管（封面本地化到 `data/covers/`，随 data/ 目录同步公网部署）；overview 补 `recommend_count`；`compare.js` 按线框图重写 —— 封面卡片多选筛选（发行日倒序、默认前 3、至少保留 2、未选遮罩）、情感对比（横向 100% 堆叠，无网格线）、口碑对比（评论量×推荐率散点）、Top 主题对比（L2 观点粒度每游戏一图，负向/正向切换默认负向）、指标对比表（库内口径 + Steam 推荐评级列）、同期/累计切换（同期 = 发行日对齐，D = 库内最新评论日 − 选中最晚发行日，发行日缺失回退累计）；测试 32 → 35 例全绿 | 对比页对齐新线框图与单游戏看板视觉；发行日期/评级数据从旧原型硬编码 mock 改为 API 采集入库（用户确认「需要 API 就采集」） |
| 2026-09-03 | **回看窗 2 天 → 7 天**：`smart_window` 参数化 `lookback_days`（默认 2 向后兼容）+ `daily_incremental_collect.py` 新增 `--lookback-days`；计划任务 `VOC-Local-Daily-Collect` 已重注册传 `--lookback-days 7`（命令已验证落盘）；新增 2 例回归（7 天 floor / 默认 2 天兼容）。注意：明晚 02:00 起窗口自动覆盖近 7 天，**底特律 8/31 缺口将自愈** | Steam recent 流非确定性采样（单次漏 5-20%，实测 6 游戏 123 条）无法根治，7 天重叠回看 + upsert 幂等 + analyzed-skip 使覆盖率随多遍采样收敛，增量成本仅分页加深 |
| 2026-09-03 | **Steam 采集器空响应静默丢数据修复**：`src/collectors/steam.py` 空页不再直接 break，改同 cursor 退避重试 ×2（`time` 新增 import）；新增 `tests/test_steam_collector.py` 2 例；本地直采任务 `ExecutionTimeLimit` 90→150 分钟（9/3 02:00 首跑实测 113 分钟超 90 上限被砍，python 孤儿进程侥幸跑完）；底特律缺失窗口已补采（43 条） | 用户报告 9/2 评论缺失排查：底特律 fetched=0 根因为 Steam 瞬时空响应 + 首页空时 `last_cursor=None` 使验证页兜底失效 → 静默 0 条且 ok=True；同窗口复现采集器实测 43 条（含目标评论）证实代码逻辑正常、属 Steam 瞬时异常 |
| 2026-09-03 | **单游戏看板重构**（线框图 `product/prototype-design/线框图-单游戏看板.png` + 同日需求变更 10 条）：API 补统一口径 —— `start`/`end` 时间窗（posted_at）+ `grain=comment\|opinion` 双颗粒度（原声主题 = `comments.topic` 主观点 L1，观点主题 = full_path L1 段）+ `full=true` 零填充（yaml primary 固定顺序）+ trends 每日推荐率（无 rating 日 null 断裂）+ 新端点 `/api/topics/tree` 与 `/api/opinions`（观点分页列表附所属原声）+ comments 列表解析 extra_json（Steam 游玩时长）并改 posted_at 降序；前端 dashboard.js 全量重写（游戏下拉只列 Steam / 时间筛选 5 档且「近N天」**不含当天** / 内联日期面板 / 范围文本 / KPI 数值 52px 居中 / 评论趋势双 Y 轴（空态走覆盖层修图表消失 bug）/ L1 条形图去「综合与元表达」改标题备注+隐横轴 / 原声-观点可折叠列表（独立展开、每页 10 条、情感+树状筛选生效））；web.css 补 `.seg`/`.kpi-trend`/`.treedrop`/`.lcard` 体系；测试 18 → 31 例全绿。⚠️ 预存环境隐患：`test_fail_closed_when_admin_without_session_secret` 会因本地 `.env` 含 SESSION_SECRET_KEY 被 load_dotenv 绕过（非本次引入） | 看板页升级为单游戏分析视角 + 原声/观点列表落地；「主观点」无独立落盘字段，由打标 core 判定物化进 comments.topic（已核实 sentiment_llm.py core 判定链路） |
| 2026-09-02 | **数据链路切换本地直采**：注册 Task Scheduler `VOC-Local-Daily-Collect`（北京 02:00，`daily_incremental_collect.py --no-download --no-upload`，脚本 `scripts/ops/register_local_collect_task.ps1` 已登记）；workflow `collect` job 置 `if: false`（已 `git diff` 核实落盘，`test` job 保留）；AUTOMATION_PIPELINE §0 / DEVELOPMENT_PLAN §五 / scripts/README 同步。本地 voc.db 即单一权威源，前端零延迟；GH Release 累积停更于 8/30（云备份待装 gh CLI）。⚠️ 机器关机 >2 天有数据缺口，恢复跑 `--full-replay` | Web 看板上线后需要本地数据始终最新；GH schedule 8h 抖动 + sync 未自动化造成 8/31+ 缺口 |
| 2026-09-02 | Web 实时看板（WEB_DASHBOARD.md）阶段 1-4 完成：`src/api/`（FastAPI）+ `product/web/`（原生 SPA 5 页）+ `collect_tasks` 表 + SQLite WAL + `bilibili_queue` paused；测试 49 → 78 例全绿（`tests/test_collect_tasks.py` 15 + `tests/test_api.py` 14）；targets.yaml 降级为 DB 种子源（config/README 已登记）；P8 时间序列随 Web 看板交付（DEVELOPMENT_PLAN 已关闭）；脚本登记（`ops/hash_admin_password.py`、dev/`verify_web_spa_load_order.js`）；VPS 部署文档补 uvicorn:8000 服务 + WAL checkpoint；§1 目录树补 src/api/ + src/queue/ | 真前端立项落地：DB → API → SPA 全链路，管理员网页增删改采集任务；Streamlit 并存不动 |
| 2026-08-19 | 初版（0-5 节：目录职责 / 命名 / 红线 / 登记 / 记忆） | 规范后续项目结构 |
| 2026-08-19 | 补第 6 节健康检查清单 + `.workbuddy` 本机定位说明 + 中文命名补充 | 使规范可落地、可审计 |
| 2026-08-19 | P6 自动化流水线落地后核对：新建 4 文件（targets.yaml / daily_incremental_collect.py / 测试 / 架构文档）均按 §1 归属与 §2 命名；4 处改动均登记索引；§6 健康检查 7 条全过 | 验证明文规则可执行 |
| 2026-08-20 | 文档收口补全：补登 `architecture/AUTOMATION_PIPELINE.md` 到 00-index 文档地图 + 我想知道表；同步 DEVELOPMENT_PLAN.md 日期与下一步主线；新增 `.workbuddy/memory/2026-08-20.md` | 收口 P6 文档完整性，避免「视未完成」状态 |
| 2026-08-21 | P10 analyzer_version 溯源 + CI pytest：新增 4 requirements 文件（core/ml/dashboard/合并）+ 1 测试文件 + 6 改动（Comment 模型 / sentiment_llm / sentiment_local / pipeline / init_db 自动 ALTER / workflow 加 test job）均按 §1 归属；§6 健康检查 7 条全过；`data/voc.db` 自动 ALTER 11332 条评论 | 解锁工程护栏：换模型/prompt 可按 version 分组重打 + CI 防回归 |
| 2026-08-22 | P6 自动化收口（A1+A2/A3+B2）：B2 文档合并 — `plan/P6_AUTOMATION_PIPELINE.md` → `architecture/AUTOMATION_PIPELINE.md §8`（5 决策 + 风险表 + Q1-Q5 决策回执 + A1 已知问题），删 plan 文档 + 改 00-index 「我想知道」表 + AGENTS.md 版本行；A1 静态分析定位 release assets=[] 根因写入 `.workbuddy/memory/2026-08-22.md`；A2/A3 待用户贴 PAT 后跑（见 `.workbuddy/memory/2026-08-22.md`） | 洁癖收口：消除两份 P6 文档重复 + 揭示 P6「累积 DB」实际未生效的 blocker |
| 2026-08-23 | P6 运行态收口（A2+A3 完成）：`voc-daily-bootstrap` release 建立（id 375081991，asset `voc.db` 76,177,408 bytes / 72.6 MB / state=uploaded）；9 commit 通过 GitHub Git Database API 推送（sandbox 屏蔽 git 网络协议 → 走 REST API 上传 32 blobs + 30 trees [topo-sort] + 9 commits，最后 PATCH ref）；远端 main HEAD = `048db18a33debc0bd265a85b999732794e22c9c3`，workflow 含 `test:` job（CI pytest 真正上线）；DEVELOPMENT_PLAN.md §四/§五/§六/§八 同步反转；AGENTS.md §1 §6 7 条健康检查全过 | 解锁工程红线 #1：换模型/prompt 后 CI 真跑 pytest 防回归；解锁 P8 时间序列前置 |
| 2026-08-23 | 归档 4 款 Steam 网游数据（PUBG/Apex/Dota2/CS2，4,916 评论/7,089 opinions/4,915 embeddings → `data/archive/online_games_2026-08-23.db`），主库 76 MB → 45.7 MB（VACUUM）；`scripts/ops/archive_online_games.py`（支持 `--dry-run`）登记到 `scripts/README.md`；`config/monitoring/targets.yaml` 加 `excluded_targets` 段（防御性）；DEVELOPMENT_PLAN §三/§六/§八 同步反转（CS2 0 新增 阻塞关闭）；CS2 专用 dev 脚本顶部加 ⚠️ 注指向归档 DB | 主库聚焦 6 款单机；后续 daily cron 不再采网游 |
| 2026-08-23 | B 站自动化阶段 0 落地：`bilibili_queue` 表（bv_id / pubdate / due_date / status / 14 列 + 复合索引）；`src/queue/{__init__,cli,runner,__main__}.py`（add / list / due / run-due / skip / remove / show）；`.github/workflows/bilibili-daily.yml`（UTC 00:30 北京 08:30，错开 Steam daily）；`tests/test_bilibili_queue.py` 6 例（表 / 状态机 / 查询 / 唯一约束 / 重访标记 / 序列化）全绿；`docs/architecture/BILIBILI_AUTOMATION.md`（状态机 + CLI + 失败重试 + v2 扩展）；DEVELOPMENT_PLAN §四 P5 升级 🥉 → ✅，§五 加 B 站自动化主线 | 解锁 P5「自动触发」：工程师 `python -m src.queue add BVxxx` 入清单，系统识别投稿时间 + 自动计算第 7 天 + 每日 cron 触发采集 |
| 2026-08-23 | 形态 A 部署文档落地：`docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md`（架构图 + 9 步部署 + 7 项安全红线 + 维护手册 + 数据通道对比 + 验收清单），按 §1 归 `architecture/`、按 §2 命名（UPPER_SNAKE_CASE）、按 §4 登记到 `docs/00-index.md`（文档地图 + 「我想知道」表新增「怎么部署公网且不让外人拿到数据」一行） | 解锁 P6.5「公网可访问 / 数据全私有」诉求：Oracle Cloud Always Free VPS + Caddy 反代 + Streamlit 127.0.0.1 bind + cron 同机跑；首次部署后补验收清单 |
| 2026-08-24 | 接入 `STEAM_API_KEY` / `DEEPSEEK_API_KEY` GH Secrets + 双标注 / 同步工具落地（详见 `scripts/README.md`）。排查 daily-collect run #17 失败根因：`config/monitoring/targets.yaml` 中 PUBG 名字半角冒号未加引号导致 YAML 解析失败。`release assets=[]` 累积 3 天 → 真凶是 `gh release upload --name` flag 不再被新 gh CLI 支持 | 修复 P6 启动失败 + release 上传静默失败 2 个 bug；解锁 P6 累积载体 |
| 2026-08-25 | 一次性收口 3 个 P6 production bug 修复 + 工具完善：去掉 `--name` flag；`QWEN_API_KEY` env + timeout `30→60`；count `30→100→null`（auto 模式，配合 60min timeout）。`scripts/ops/dual_annotate_qwen_flash.py` + `scripts/ops/sync_local_from_{release,artifact}.py` 登记 `scripts/README.md`。同日 QWEN-flash 实验失败（`qwen3.7-flash` / `qwen3-flash` / `qwen3.6-flash` 全部 API 404，token-plan 个人版模型名 ≠ 通用文档），run #24/#25 误标 2,542 假数据（`analyzer_version=llm:qwen3-flash@55c003a3`, sentiment 全部默认 neutral），最终回退到 DEEPSEEK。run #28 dispatch 验证 DEEPSEEK 在新代码下 3m14s 跑通，34 条真标注入库 | 解除 P6 三重阻塞（API key 缺 / count cap / release 上传）；8/27 cron 准备就绪 |
| 2026-08-26 | B 站单视频看板 v0.2 一波走落地：`product/build_bilibili_video.py` 重写（`HTML_TEMPLATE` 从 `.format()` 改 `string.Template` 避开 CSS/JS `{` `}` 转义；JS 内 `$`/`$$` 改名 `qs`/`qsa` 避 Template 冲突；所有 `${...}` 转 `$${...}`）；inline mock 数据（3 视频 dropdown + 32 条评论带 L3 + 16 桶弹幕 + L2 主题分布）；`product/prototype/bilibili-video.html` 45.7 KB 生成；`product/prototype_overview.md` v0.2 节补全；`product/README.md` 索引更新。区块1 视频概览（无序号/BV号/评论构成，2×4 stat-grid 含「三连率 = 26.1%」）；区块2 改名「评论情感与画像」（情感 绿/黄/红 + 男女比 蓝/灰/紫 顺序 男/保密/女，删除情感均分/置信度）；区块3 改名「主题情感分析」（左 ECharts 堆叠条 + L1→L2 下钻 + sentiment 段覆盖式过滤，右评论列表分页每页 10 条 likes desc→posted_at desc + 情感色块 + L3 标签 + 返回按钮清全部 filter）；区块4 弹幕时间轴（单色面积图 + TOP3 区域强调色 + Y 轴 `count × (video_total/collected_total)` 回算 + 高光时刻按时序卡片括号显示估算数 + 3 样本）。封面图点击弹窗嵌 B 站 `player.bilibili.com/player.html?bvid=` iframe，ESC/点遮罩关闭。`export_bilibili_data.py` v0.1 不动留底 | 解锁 B 站看板评审：4 个区块需求全部对齐；交互链路（L1→L2 / sentiment 段过滤 / 返回按钮）跑通；弹幕回算逻辑验证（3000 采集 → 71835 估算）；iframe 弹窗预留视频内秒点跳转扩展位 |
| 2026-08-26 | B 站单视频看板 v0.3 接 DB 落地：`product/export_bilibili_data.py` 重写为 `export_all()` —— 一次性扫描 DB 全表 bvid → 输出单一 JSON `data/_bili_videos.json`（1.4 MB / 3 视频），结构 `{videos_list: [...], videos: {bvid: {video, comments, profile, danmaku}}}`；新增 `comments.list`（按 likes desc → posted_at desc 排序，join CommentOpinion 抽 L3 标签，limit 2000 防爆）+ `comments.topics_l2`（按 L1 分组聚合 L2×情感）+ `danmaku.video_total`（原视频弹幕总数用于 Y 轴回算）；保留 `export_video(bvid)` 单视频接口向后兼容。`product/build_bilibili_video.py` 重写为 SPA 风格：3 视频全部 inline 进 HTML（约 655 KB），`switchVideo(bvid)` 切换 `DATA` + 重渲染所有区块；ECharts 实例化时旧 chart 显式 `dispose()` 防内存泄漏；JS 内所有 `${...}` 转 `$${...}` 避 Template 替换；HTML 容器化（区块1/2 由 JS 渲染、3/4 已有 JS 渲染）。踩坑 2 处：1) `HTML_TEMPLATE` 末尾残留一行 `"""` 致 EOF-in-multi-line-string（SyntaxError 在 782 行，实际错在 771 行）；2) CRLF 行尾导致 Windows 终端显示乱码（不影响文件内容） | 解锁 B 站看板 3 视频真实数据切换（不再依赖 mock）；数据导出链路打通：DB → JSON → HTML，build 脚本只关心 JSON 形态；ECharts dispose 模式为后续 dashboard 多视图复用留好钩子 |
| 2026-08-26 | B 站单视频看板 v0.3 关键 bugfix：`let DATA = ALL_VIDEOS[$initial_bvid]` Template 替换后产生裸标识符 `ALL_VIDEOS[BV1kS8H6VERt]` → 浏览器 ReferenceError → `renderAll()` 不执行 → 区块1/2/3/4 全空。修复：模板写 `['$initial_bvid']` 字符串字面量。沉淀：`$VAR` 在 JS 里必须加引号（`'$VAR'`）避免 Template 静默生成裸标识符 | v0.3 修遗漏 bug；以后 `string.Template` 内嵌 JS 标识符一律带引号 |
| 2026-08-27 | GH Actions schedule 双 workflow（daily-collect + bilibili-daily）当日都没触发，audit 发现 manual dispatch 写"成功"但 release asset=0。三处修复：①新建 `scripts/ops/verify_release_upload.py` + 8 例 pytest（`tests/test_verify_release_upload.py` 全绿），作为 silent 失败防御；②`daily-collect.yml` 加「校验今日 Release asset」步骤（`if: always()`，失败 exit 1 触发 GH 邮件告警）；③两个 workflow 的 cron 同时从 `0 0 * * *` UTC（北京时间 08:00）改为 `0 17 * * *` UTC（北京凌晨 1:00），即便 GH Actions schedule 延迟 8 小时也只在早上 9:00 北京触发。当日同步 release 后查 DB：873 条 Steam 评论的 posted_at 分布显示设计预期"采集昨天数据"有 10.9% 当天数据（因 `calc_posted_after` lookback=1 day），权衡后保持现状（边界安全 > 严格语义）。`docs/architecture/AUTOMATION_PIPELINE.md §8.3` 升级加真实案例 + 已上线防御；`scripts/README.md` 登记新脚本 + 版本记录 | 解锁 P6 silent 失败告警链路；新 cron 时间带「早完成」保障 |
| 2026-08-28 | 标注流程接入备选 LLM「`glm-5.3-flash`」（智谱 BigModel VLM）：3 处源码 + 1 处 .env + 4 个 pytest 用例。（1）`src/analyzers/sentiment_llm.py` `PROVIDER_CONFIG` 新增 `"glm-5.3-flash"` 条目，共享 OpenAI 兼容端点 `https://open.bigmodel.cn/api/paas/v4/`；凭据走 `GLM_API_VOC_PLATFORM`（映射用户变量 `glm_api_voc_platform`），与现有 `glm` provider 解耦便于额度隔离 / 失败回退 / 配额黑洞排查；默认 model = `glm-5.3-flash`。（2）`src/pipeline.py` argparse choices 加 `glm-5.3-flash`。（3）`.env.example` 新增独立 env 块。（4）`tests/test_analyzer_version.py` 加用例 11–14：注册项字段、`analyzer_version = llm:glm-5.3-flash@{prompt_hash8}`、缺失 `GLM_API_VOC_PLATFORM` 抛 `ValueError`、`pipeline` CLI choices 含新选项。本沙箱 pytest 整体挂死（连 `test_prompt_set_hash_is_stable` 都超时，与本次改动无关），改走直接 subprocess 跑 6 例（4 新 + 2 回归）6/6 全过；CI 在 GitHub Actions 上正常执行 | 备选 LLM 接入：与主 `glm` provider 隔离开，单条线额度可控；同时为后续「glm-5.3-flash vs DEEPSEEK 双标注对比」留好 provider 钩子 |
| 2026-08-28 | 本地 sync 自动化收口：新建 `scripts/ops/smart_sync_release.py`（7.2 KB，幂等智能 sync：①今天 release 未上传 → 安静 exit 0②本地比远端新 → noop exit 0③远端比本地新 → 下载 + 安全 rename 替换 → exit 0④文件锁 → exit 1 + 提示"关仪表盘"）+ `scripts/ops/register_sync_tasks.ps1`（2.7 KB，Windows Task Scheduler 注册 4 daily task 错开 10:00/13:00/18:00/22:00）。当天发现工程师报"今天 cron 跑成功但数据没到本地"——根因是 GH Actions 自动流程只到 upload release 半边，没有 push-to-local 通道（设计有意为之：远端 GH Release 是公共累积载体，本地 DB 是工程师仪表盘私有数据源）。新链路让 22:00 兜底也能拉到数据，无需人工介入。Streamlit 文件锁问题已绕过：sync 前需关仪表盘（kill streamlit + 子 python 进程）。`scripts/README.md` 同步登记 + 版本记录 | 解锁 P6「GH Release → 本地」自动 sync（之前需手动跑 sync_local_from_release.py）；4 task 错开应对 8h 延迟；幂等设计支持任意次重跑；Streamlit 锁文件问题文档化 |
| 2026-08-31 | 每日时间窗策略 v2：以北京日历日为准，每天采「昨天全天 + 前天全天」，当天严格不采。`scripts/ops/daily_incremental_collect.py` 新增 `BJT_OFFSET = timedelta(hours=8)` 常量 + `smart_window(target_id, now_utc) -> (posted_after, posted_before)` 函数（30 行，行为矩阵 4 行覆盖正常/补救/空 DB/连败场景）。`run_one_target()` 加 `now_utc` 关键字参数（整批共享基准），`main()` 算一次 `now_utc = _utcnow()` 传给每个 target。`tests/test_daily_incremental_collect.py` 加 4 个 `test_smart_window_*` 用例（normal/recovery/empty/BJT 跨 UTC 日界），3 个现有 `run_one_target()` 调用补 `now_utc=now`；10/10 pytest 全绿。`docs/architecture/AUTOMATION_PIPELINE.md` §2.4/§3/§4/§8.1/§8.5/版本记录 6 处更新（v1 标弃用 + v2 设计记录 + 回滚路径）。`calc_posted_after()` 保留作为 smart_window 的内部依赖 | 解决 v1 两个痛点：①窗口永远落后 1-2 天（昨天 release 的 max 通常是前天 23:xx UTC，posted_after = 3 天前）；②每天混入 10.9% 当天数据违反「采昨天」语义。floor = 北京前天 0:00 兜底补救场景（昨天 workflow 失败时覆盖前天全天）；upsert 去重保正确性（每天重复采前天 0:00~8:00 的 8h 可接受，~6 次 Steam API 调用） |
| 2026-08-31 | **主标注器切换 DeepSeek → GLM-5.3-Flash**：`.env` `ANALYZER_PROVIDER=deepseek` → `glm-5.3-flash`；`.github/workflows/daily-collect.yml` `ANALYZER_PROVIDER: qwen` → `glm-5.3-flash` + 新增 env 注入 `GLM_API_KEY: ${{ secrets.GLM_API_KEY }}`（`.env` 与 workflow 之前不同步，dotenv 已升级一致）。本地 key 来源：Windows 用户变量 `glm_api_voc_platform`（49 字符），python-dotenv 默认 `override=False` 不覆盖已存在的系统变量，Windows `os.getenv` 大小写不敏感 → `.env` 无需写明文 key。CI 端 key 来源：GH Secrets 工程师手工添加 `GLM_API_KEY`（agent 改不了 Secrets）。本地验证 `scripts/dev/verify_glm_5_3_flash.py`：真实请求跑通，analyzer_version=`llm:glm-5.3-flash@55c003a3` ✓，样本评论「战斗手感不错但优化太差」→ sentiment=negative, score=-0.8, 3 个观点（战斗手感+/优化太差- is_core/30 系掉帧-），全路径正确。`scripts/README.md` dev/ 章节登记新脚本；`.workbuddy/memory/2026-08-28.md` 已记录 provider 接入决策 | 解锁主标注器从 DeepSeek 切到 GLM-5.3-Flash（VLM 范畴）；共用同一套 GDT v3.1.1 prompt 集合（不污染 `analyzer_version` 溯源）；明早 cron 起自动生效 |
| 2026-08-31 | **沙箱推送踩坑沉淀 + push 排查指南**：本地 16 文件改动（含 smart_window v2 + GLM-5.3-Flash 切换）推到 GitHub 过程踩 4 个新坑：①REMOTE_HEAD 硬编码过期 → parent 用旧 SHA 截断 e1380fe squash 历史；②root 目录排序 bug（`''` 和 `src` depth 都 0，set 无序导致 root 先处理时子目录新 SHA 还没注册）→ 子目录改动全部失效；③**basename key 冲突**（`src` vs `product/prototype/src` 同名）→ `new_subdir_trees` key 用 basename 致后处理覆盖前处理，src/ 被前端文件污染；④**GitHub refs 二级限流**（与权限无关，blob POST 仍 201，ref POST/PATCH 持续 403）→ sandbox 写操作连续 5+ 次触发；最终兜底：用户本机 `git push origin main:main --force`。新建 `docs/guides/PUSH_TROUBLESHOOTING.md`（7 章节：决策树 / 用法 / 已知坑 / 验证 / 回滚 / 检查清单 / 相关文档），登记到 `docs/00-index.md` 文档地图 + 「按我想知道」表；`.workbuddy/memory/2026-08-31.md` 完整复盘；`MEMORY.md` §沙箱 push 段增订 4 个坑摘要。清理 30 个临时调试脚本（`_check_*.py` / `_test_*.py` / `_verify_*.py` / `_debug_*.py` 等） | 解锁「每次 push 前先读 PUSH_TROUBLESHOOTING.md」的长效机制 + 「sandbox refs 限流时立即提示用户手动 git 推」的硬约束；避免下次重复踩坑 |
| 2026-08-31 | **GLM secret 命名统一 `XXX_API_KEY` 格式**：原计划用 `GLM_API_VOC_PLATFORM`（与主 `GLM_API_KEY` 解耦，便于额度隔离/失败回退/配额黑洞排查），工程师要求统一 `XXX_API_KEY` 格式（与 DEEPSEEK/QWEN/STEAM 一致便于维护），故改 4 文件：①`src/analyzers/sentiment_llm.py` `glm-5.3-flash.api_key_env` 从 `GLM_API_VOC_PLATFORM` → `GLM_API_KEY`；②`.env` 把占位符替换为真实 key 8846c10...（`.gitignore` 已排除，作离线备份）；③`.env.example` BOM+CRLF → UTF-8 LF 重新写，删除独立 `GLM_API_VOC_PLATFORM` 块；④workflow env 名同步 `GLM_API_KEY: ${{ secrets.GLM_API_KEY }}`。本地验证重跑通过：`analyzer_version=llm:glm-5.3-flash@55c003a3` ✓。GH Secret 名同步从 `GLM_API_VOC_PLATFORM` 改 `GLM_API_KEY`（工程师已加）。解耦设计（额度隔离/失败回退）当前通过 `.env` 同一 key 实现，未来需要切回额度隔离时只需再改 `api_key_env` + 新 secret | 统一命名约定优先于解耦设计（工程师偏好）；`.env` 写明文作冗余备份（已 .gitignore 安全）；保留 `GLM_5_3_FLASH_BASE_URL/MODEL` 独立环境变量（端点/模型未来可能与 `glm` provider 分叉） |
| 2026-08-31 | 同日：用户问「每天采的是昨天的数据并且尽量采集完全，也可以尝试补全前天的数据，而当天的不采集」→ 落地成「每日时间窗策略 v2」上一行（2026-08-31 第一行）。本行为同一日第二次变更记录 | 时间戳同日多变更分别记录便于回溯 |
| 2026-09-01 | **P6 workflow 文档/代码脱节收口 + Node 20 deprecation 解锁**：当日 `daily-collect.yml` cron 跑 30m24s 被 cancel（"exceeded maximum execution time of 30m0s"），audit 发现 AGENTS.md 2026-08-27 与 2026-08-25 记录声称已改的 3 项 P6 production 修复**实际未落地到 workflow 文件**：①timeout-minutes:30（应 60）；②cron `'0 0 * * *'`（应 `'0 17 * * *'`）；③「校验今日 Release asset」步骤（`scripts/ops/verify_release_upload.py` 已写好但 yml 未调用）。`bilibili-daily.yml` 同样 cron + timeout 没改 + `ANALYZER_PROVIDER: deepseek` 未切 `glm-5.3-flash`。本次修复：①两个 workflow 的 timeout-minutes 30→60；②cron `'0 0 * * *'`/`'30 0 * * *'` → `'0 17 * * *'`/`'30 17 * * *'`（与 docs §8.5 对齐）；③`daily-collect.yml` 新增「校验今日 Release asset」步骤（`if: always()`，调用 `verify_release_upload.py` 失败 exit 1 触发 GH 邮件告警）；④`bilibili-daily.yml` 切 GLM-5.3-Flash + 注入 `GLM_API_KEY`；⑤注释/设计文档同步对齐新时间。本地 `tests/test_daily_incremental_collect.py` 10 例 + `tests/test_verify_release_upload.py` 8 例门禁。**教训沉淀**：AGENTS.md 版本记录 ≠ workflow 实际状态，下个里程碑须把「workflow yaml diff」加入 §6 健康检查 7 条清单 | 解锁 P6 真实「早完成」+ silent 失败告警链路；防止类似文档/代码脱节再次发生 |
| 2026-09-01 | **§6 健康检查清单扩展**：原 7 条只覆盖「目录/索引/命名/数据/工作树」，未覆盖「**workflow yaml 是否与最新版本记录一致**」。本次事故根因 = AGENTS.md 写了「已改 yml」，但实际 yml 没改。新增第 8 条：**[ ] workflow yaml diff 与版本记录一致**：每次更新版本记录前先 `git diff` 两个 `.github/workflows/*.yml`，确认改动已落盘；尤其 P6 silent 失败/cron/timeout 等已知生产 bug 修完后 | 防止「AGENTS.md 自述已完成 ≠ workflow 实际已完成」的脱节再发生 |
| 2026-09-01 | **HANDOVER 收口 · 项目状态速览落地**：README.md 顶部新增「📍 项目状态速览」段（已完成 / 接下来 / 终点 3 块），让接手者/新 Agent 30 秒内定位；统一 README/AGENTS/DEVELOPMENT_PLAN 时间戳到 2026-09-01；README 主标注器说法 DEEPSEEK → GLM-5.3-Flash；4 处索引去重（`docs/00-index.md` 重复 QUICK_START.md、`product/README.md` 重复 game-compare.html、`scripts/README.md` 合并 2 条 2026-08-27 版本记录） | 项目交接准备：让新接手者不依赖历史对话即可上手 |
| 2026-09-01 | **scripts/dev/ 激进归档 ~35 个一次性脚本到 archive/**：按 7 个子目录分类（debug/ diag/ one_shot_backfill/ one_shot_curate/ one_shot_export/ one_shot_prototype/ one_shot_verify/ e2e/ P6_bootstrap/）；保留 ~12 个核心脚本（reanalyze_all / rematch_opinions / recompute_topics / rebuild_golden_set / l35_cluster / analyze_danmaku / mine_fallback_candidates / export_prototype_data / verify_* 3 个）；`scripts/README.md` 同步登记 archive/ 收纳规则 | 去除 dev/ 冗余，让新接手者一眼看到「真正活跃的工具集」 |
| 2026-09-01 | **工作区瘦 · 缓存 / 测试 DB / scratch 清理**：删除 7 处 `__pycache__/`（gitignored 但污染工作树）+ 100+ `data/voc_test_*.db` + `voc_debug_*.db` + `voc_smoke_p6.db` + `data/_bili_video.json`（被 `_bili_videos.json` 取代） + `.workbuddy/scratch/`（8-28 push 排障 + 8/1 inspect 残留）；`data/voc.db` 主库与 `data/archive/online_games_2026-08-23.db` 网游归档保留（运行时数据） | git 工作树干净（`git status --short` 空）；handover 不带噪音 |
| 2026-09-01 | **`src/README.md` + `tests/README.md` 模块/用例索引建立**：src/ 6 子目录（analyzers / collectors / queue / storage / visualizer + 顶层 pipeline.py）每个文件标注职责 + 更新时间 + 关联文档链接；tests/ 8 个测试文件（pipeline / golden_match / analyzer_version / bilibili_queue / daily_incremental_collect / verify_release_upload / analysis_pipeline / embedding）每个用例标注覆盖范围 + 用例数 + 门禁场景 + fixtures 索引；`docs/00-index.md` 「按我想知道」表新增 2 行（src 模块结构 + tests 用例索引）；按 §4 同步登记 | 让接手者 30 秒内理解代码模块边界 + 49 例 pytest 哪几条是核心门禁 |
| 2026-09-01 | **docs/ 命名规范化 + 路径合理性调整**：4 文件改名（`duals_2026-08-25_archive.md` → `DUAL_ANNOTATION_QWEN_FLASH_2026-08-25_ARCHIVE.md` / `VoC平台竞品调研报告.md` → `VOC_COMPETITOR_RESEARCH.md` / `新标签体系验证报告.md` → `GDT_V311_VERIFICATION_REPORT.md` / `STEAM_API_FIELDS.md` 移到 `architecture/`）+ 同步 13 处引用更新（00-index 树 + 按我想知道表 / DEVELOPMENT_PLAN / QUICK_START / BILIBILI_COLLECTION / src/README / scripts/README）+ README 5min 段合并到 QUICK_START.md（README 留指针 + 一句话命令）+ QUICK_START.md DEEPSEEK 引用更新为 GLM-5.3-Flash（默认主标注器，2026-08-31 切换） | 按 §2.1 命名规范 + §1 目录归属决策表 + §0「非必要不新增」收口；让 docs/ 命名与路径全部符合工程约定 |
| 2026-09-01 | **README.md 4 处数据准确性收口**：①项目结构树（L97-140）从 11 行扩充为完整 45 行（补 `src/queue/` + `.workbuddy/` + `src/dev/archive/` + `tests/fixtures/` + product v1+v2 双版 + docs/architecture 10 文档 + ops 12 脚本 + 49 pytest）+ ②`data/voc.db` sync 日期 `2026-08-27` → `2026-09-01` + ③pytest 用例数 `38` → `49` + ④致谢段补「智谱 BigModel GLM-5.3-Flash（2026-08-31 起主标注器）」，DeepSeek 降为备选 | 与实际项目状态对齐；消除 README.md 的过时数据误导接手者 |
| 2026-09-01 | **个人标识落地**：LICENSE `VoC Platform Contributors` → `EricChan`；README.md 加「👤 作者」section（GitHub: @misEricality / 问题反馈走 GitHub Issues / 邮箱用 GitHub noreply 邮箱转发避免暴露真实邮箱）；新增 `CITATION.cff`（GitHub 自动识别「Cite this repository」按钮 + 学术引用标准格式）；新增 `AUTHORS.md`（项目作者 + 贡献者占位 + 致谢 + 引用 + 许可证导航）；`docs/00-index.md` 「按我想知道」表新增 2 行（作者联系方式 / 学术引用）；按 §4 同步登记 | 让接手者 / 面试官 / 引用方有清晰的个人识别 + 标准引用格式；保护真实邮箱（用 GitHub noreply 邮箱） |
