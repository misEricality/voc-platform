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
| 2026-09-11 | **洁癖收尾（知识面）**：①**版本记录迁出** —— AGENTS.md 的「版本记录」累积 73 行、单行可达数千字符，把 224 行的规则文件撑到 92 KB；按「规则文件只放下次 Agent 不看到就会犯错的边界」把 **70 条历史整段迁到 `docs/CHANGELOG.md`**（已登记进 `docs/00-index.md`），AGENTS.md 只留最近 3 条 + 指针（**保留同一标题**，既有 `#版本记录` 锚点不失效）；②**修 2 条死链**（AGENTS.md §6 点名的类型）：`DATA_STORAGE_DESIGN.md` 的 `./DEVELOPMENT_PLAN.md` → `../plan/DEVELOPMENT_PLAN.md`（文件早已移位）、`SELF_HOSTED_VPS_DEPLOYMENT.md` 的 `../SECURITY.md`（**该文件从未存在**）→ 改指本文 §5 + AGENTS.md §3；③删 `STATIC_SNAPSHOT_DEPLOYMENT.md` 里**重复的版本记录标题**（历史编辑残留）；④`src/README.md` 补 `runtime_mode.py`（模块地图 + 小节）—— AGENTS.md §1 当时补了、这个二级索引漏了；⑤**清理 `data/` 测试残留 2083 个文件 / 316.9 MB**（`voc_test_*` / `voc_chat_loop_*`；先停本地 web 服务再删，生产库 `voc.db` 完好：integrity=ok、comments=18916、collect_tasks=8）；⑥**线上部署代码 = 本地 HEAD 得到字节级证明**：4 个文件的 git blob SHA1 逐字一致，线上行为实测（无凭据 401 / 有凭据 200 / admin 写 403 / `/docs` 404）；死引用复扫 **0 条**、全量 pytest **257 passed / 1 skipped** | 工程师「1. 版本记录搬出。2. 先终止本地web服务，然后消除 `data/` 测试残留。3. 修正完成后，提交。」 |
| 2026-09-11 | **填掉「线上 admin 页看着能采」的陷阱（展示端代码级收口）+ 修 `LOG_LEVEL` 空转**：①新增 `src/runtime_mode.py::display_only()` —— **默认跟随 `PUBLIC_MODE`**（公网形态本身就是展示端，故 **VPS 的 `.env` 不需要加任何键**，少一个能忘的地方；本地 `PUBLIC_MODE=0` 不受影响；确需一台可写的公网实例才显式 `DISPLAY_ONLY=0`）。②`src/api/routers.py` 新增依赖 `require_writable` 挂 `admin_router`（置于 `require_admin` **之后**）：GET/HEAD/OPTIONS 放行（线上仍能"看"任务列表与 backfill 状态），POST/PATCH/DELETE 一律 **403**；未登录仍先得 **401**（不向匿名者透露实例形态）；因为它是**依赖**，对不存在的 id 也返 403 而非 404（拒绝理由是"实例只读"，与行是否存在无关）。③`src/pipeline.py::run_pipeline()` 开头直接 `raise` —— **采集与标注一起挡**，且连采集器初始化都不进；B 站队列 `runner` 走同一个函数，同样覆盖。④`src/api/main.py` 启动往日志写一行形态声明（`grep 展示模式 logs/web.log` 即可确认这台能不能写/能不能采）。⑤**顺带修掉一个真问题**：uvicorn 只给 `uvicorn.*` 配 handler 且 `propagate=False`，**root logger 一直没 handler** → `src/api/*` 的 `log.info` 全被 Python lastResort（只收 WARNING+）丢弃；**实测线上 `logs/web.log` 里一条 `voc.api` 日志都没有**（只有 uvicorn 访问行），`.env` 里 `LOG_LEVEL=INFO` 纯属空转 → `create_app` 在 `load_dotenv` 之后按 `LOG_LEVEL` 配 root（`basicConfig`，root 已有 handler 时自动 no-op，不与 uvicorn/pytest 抢配置）。⑥测试 **+5**（`test_p1_hardening.py` 3 例：拒绝 pipeline / 默认继承 PUBLIC_MODE / 日志配置；`test_api.py` 2 例：写 403 而读 200 / 未登录仍 401）→ 全量 **257 passed / 1 skipped**。⑦**VPS 实测（全程零副作用，只打不存在的 id 与非法 BV）**：登录 200、`GET /api/admin/tasks` 200、`PATCH`/`DELETE` 不存在 id **403**（未收口时是 404）、`POST` 非法 BV **403**（未收口时是 422，绝不会真建任务）、未登录 `POST` **401**；`collect_tasks` 仍 8 行无人写入、SPA/`/api/health`/`/api/targets` 200、`/docs` 仍 404；日志出现形态声明。**配套运维口径（已写入手册 §0.5/§5.5.3）**：线上 admin 页只当"查看"用，采集任务只在本地改，要即时生效就手动跑 `push_db_to_vps.ps1`；**VPS 上产生的 Agent 会话会在次日 02:00 整库推送时被覆盖**（本次按工程师决定暂不处理）。 | 工程师「把这个陷阱填掉……VPS 不要采集，也不要标注」+ 确认「线上 agent 记录暂不处理」 |
| 2026-09-11 | **内测前收尾：补丁清零 + 重启换内核 + SSH root 收敛**：①`apt-get dist-upgrade` 装掉余 5 个（内核 / `linux-firmware` / `fwupd`，**0 删除、27 新装**）→ **剩余可升级 0**；②`reboot` 后内核 `6.8.0-124` → **`6.8.0-139`**、`reboot-required`（`libc6`/`apparmor`/`linux-base`）清零；③`PermitRootLogin` 由 `yes` 改 **drop-in** `sshd_config.d/99-voc-hardening.conf` —— Ubuntu 的 `sshd_config` 在**顶部** `Include` 该目录、sshd 取"**先出现者生效**"，所以 drop-in 能覆盖主文件里的 `yes`，且不受包升级 conffile 影响；流程为 写 drop-in → `sshd -t` 校验（不过即删掉回退）→ `reload ssh`（不重启、不断连接），实测 `permitrootlogin without-password`；④**重启后复验**：6 服务全 active+enabled、无凭据 401 / 有凭据 200 / `/docs` 404 / 明文 `:8443` 400、5 项安全头仍在、**内部 CA 证书复用未重签**（测试者不会看到新证书警告）、主库 `comments=18916`、审计库完好、公网侧 curl 同样通过 | 工程师「1. 重启，现在。2. 改。」 |
