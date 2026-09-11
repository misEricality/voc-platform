# AGENTS.md — 项目工程约定（每次会话必读）

> **这份文件是代理（Agent）与工程师共同遵守的唯一工程规范来源。**
> 每次新会话开始时，代理必须先读完本文件再动手，无需工程师重复强调。
>
> **最后更新**：2026-09-11

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
│   ├── runtime_mode.py       运行形态判定（展示端 = 不采集/不标注/不改任务）
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
- 本文件的约定若变更，须在下方「版本记录」登记并同步记忆。**该表只保留最近 3 条**，更早的历史在 `docs/CHANGELOG.md`（2026-09-11 迁出，避免规则文件被 changelog 撑大）。

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

> **本表只保留最近 3 条。** 历史（70 条，含 2026-08-23 起）已迁至 **[docs/CHANGELOG.md](docs/CHANGELOG.md)** —— 规则文件是「下次 Agent 不看到就会犯错的边界」，不再兼作 changelog（2026-09-11 搬迁）。新增条目写在**最上面**；超过 3 条时把最旧的移入 CHANGELOG。

| 更新时间 | 内容 | 原因 |
|---|---|---|
| 2026-09-11 | **删除云端采集 workflow（仓库只留 CI 门禁）**：①`.github/workflows/daily-collect.yml`（Steam 采集，其 `collect` job 早在 2026-09-02 已 `if: false`）与 `.github/workflows/bilibili-daily.yml`（B站 `run-due`，**无任何停用标记**、配置层面每天 UTC 17:30 仍被调度）**一并删除**；新增 `.github/workflows/ci.yml` 收敛原两份里重复的 `test` job，触发从「只有 schedule + workflow_dispatch」改为 **`push`(main) / `pull_request` / 手动** —— 顺带修正原 `daily-collect.yml` 注释里「push 时也能早期发现回归」的空头承诺（当时 `on:` 里根本没有 push）。②删除依据：数据链路自 2026-09-02 起为**本地直采**（`VOC-Local-Daily-Collect` 北京 02:00，2026-09-05 起内含 B站 `run-due`，链末尾 `--push-db` 推 VPS），云端采集属重复劳动 —— 烧 GLM 标注 token、B站多一次风控暴露，且产物只进 GH artifact（30 天）与 `voc-daily-bootstrap` release、**不回流**本地权威源。③**吞吐变化（需知情）**：B站单日上限由原 workflow 的 `--limit 50` 降为本地 `limit=5`（防风控），到期任务超 5 条会顺延次日；要更快用 `python -m src.queue run-due --limit N` 或调大 `--bili-limit`。④文档/代码同步（只改现役说法，历史记录保留）：`AUTOMATION_PIPELINE.md`（顶部状态、§0 现役、文件清单、§8.3 防御失效标注）、`BILIBILI_AUTOMATION.md`（§1.3 改写为本地计划任务 + 吞吐变化）、`DEVELOPMENT_PLAN.md`（现状表 + 两条现状表述）、`WEB_DASHBOARD.md`、`README.md`、`tests/README.md`、`scripts/README.md`、`PUSH_TROUBLESHOOTING.md`、`SELF_HOSTED_VPS_DEPLOYMENT.md`（「关闭 workflow」那步已无需执行）、`scripts/ops/daily_incremental_collect.py`（docstring 调用方改为本地计划任务）、`scripts/ops/sync_local_from_artifact.py`（标注上游已删、无 artifact 可拉）、`scripts/ops/push_via_api.py`（验证清单文件名 → `ci.yml`；原值会让脚本最后一步 FileNotFoundError）。⑤测试全绿、死引用扫描 0 条 | 工程师「如果GitHub workflow采集已经用不着，把它们取消掉」 |
| 2026-09-11 | **修掉限流 429 误伤（前端去重 + 阈值上调）**：审计库实测 **33/346 = 9.5% 请求被 429 拒**，全部来自同一出口 IP，`13:07` 峰值 **116 req/min** —— 是**正常浏览**（切游戏/切筛选）撞穿 120 的闸门，不是攻击。根因：**刷新没有合并** —— 一次触发就发满一轮（overview+trends+topics[+comments]），实测「1 秒内 10 次 refreshCharts」。修法：①`src/api.js` 加**同 URL 在途去重**（settle 即摘除、**不产生缓存**；非 GET 不合并；失败不粘住）②`pages/dashboard.js` 的 `refreshCharts`/`refreshList` 加**在途合并**（进行中再来触发只记一个标记、结束后**补跑一次**而非 N 次）③`PUBLIC_RATE_LIMIT_PER_MIN` 120 → **400**（线上原本**没有这个键**、走的是默认 120，现显式写入；本地同步）。验证：Node 单测 **7/7**（合并 / 不缓存 / 失败可重发 / POST 不合并）；Playwright 实测**连续 8 次触发 → `/api/overview` 只 +2、API 总请求 +8（未合并会是 +32）**，看板渲染完好、控制台仅既有 favicon 404；线上**连打 130 次 `/api/targets` → 200=130 / 429=0**（改前第 121 次必 429）；`AGENT_RATE_LIMIT_PER_MIN` 未动。前后端缓存串已 bump（`api.js?v=20260911b` / `dashboard.js?v=20260911c`） | 工程师「1和2一起做，然后同步VPS和本地」 |
| 2026-09-11 | **洁癖收尾（知识面）**：①**版本记录迁出** —— AGENTS.md 的「版本记录」累积 73 行、单行可达数千字符，把 224 行的规则文件撑到 92 KB；按「规则文件只放下次 Agent 不看到就会犯错的边界」把 **70 条历史整段迁到 `docs/CHANGELOG.md`**（已登记进 `docs/00-index.md`），AGENTS.md 只留最近 3 条 + 指针（**保留同一标题**，既有 `#版本记录` 锚点不失效）；②**修 2 条死链**（AGENTS.md §6 点名的类型）：`DATA_STORAGE_DESIGN.md` 的 `./DEVELOPMENT_PLAN.md` → `../plan/DEVELOPMENT_PLAN.md`（文件早已移位）、`SELF_HOSTED_VPS_DEPLOYMENT.md` 的 `../SECURITY.md`（**该文件从未存在**）→ 改指本文 §5 + AGENTS.md §3；③删 `STATIC_SNAPSHOT_DEPLOYMENT.md` 里**重复的版本记录标题**（历史编辑残留）；④`src/README.md` 补 `runtime_mode.py`（模块地图 + 小节）—— AGENTS.md §1 当时补了、这个二级索引漏了；⑤**清理 `data/` 测试残留 2083 个文件 / 316.9 MB**（`voc_test_*` / `voc_chat_loop_*`；先停本地 web 服务再删，生产库 `voc.db` 完好：integrity=ok、comments=18916、collect_tasks=8）；⑥**线上部署代码 = 本地 HEAD 得到字节级证明**：4 个文件的 git blob SHA1 逐字一致，线上行为实测（无凭据 401 / 有凭据 200 / admin 写 403 / `/docs` 404）；死引用复扫 **0 条**、全量 pytest **257 passed / 1 skipped** | 工程师「1. 版本记录搬出。2. 先终止本地web服务，然后消除 `data/` 测试残留。3. 修正完成后，提交。」 |
