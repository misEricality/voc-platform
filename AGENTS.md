# AGENTS.md — 项目工程约定（每次会话必读）

> **这份文件是代理（Agent）与工程师共同遵守的唯一工程规范来源。**
> 每次新会话开始时，代理必须先读完本文件再动手，无需工程师重复强调。
>
> **最后更新**：2026-09-21

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

> **本表只保留最近 3 条。** 历史（76 条，2026-09-01 ~ 2026-09-11）已迁至 **[docs/CHANGELOG.md](docs/CHANGELOG.md)** —— 规则文件是「下次 Agent 不看到就会犯错的边界」，不再兼作 changelog（2026-09-11 搬迁）。新增条目写在**最上面**；超过 3 条时把最旧的移入 CHANGELOG。

| 更新时间 | 内容 | 原因 |
|---|---|---|
| 2026-09-30 | **修掉「长评论批次把整条标注链打挂」的输出截断问题（新增 13 例回归）**：①**定位**：为评估「把标注模型换成 glm-5.3-flash」做受控对比（62 条真实评论 × 2 模型 × 2 轮）时发现 **DeepSeek 侧 13 批炸 3 批（30 条评论）**——`finish_reason=length`、`completion_tokens` **正好 2500**（撞死 `MAX_OUTPUT_TOKENS`），JSON 被拦腰截断。②**放大机制（真正的缺陷）**：`_parse_batch` 整段解析失败后会退到贪婪正则 `\{.*\}` **再 `json.loads` 一次，而这次没有任何保护** → `JSONDecodeError` 穿透 `analyze_batch`；配合 2026-09-21 主链路的 `raise_on_error=True`，把「一次输出太长」放大成「整个 target 当天失败」（靠次日哨兵补采兜，且次日补采剩余条数少、不再撞限 → **失败被自愈吞掉、不留长期痕迹**）。③**修法**：`MAX_OUTPUT_TOKENS` 2500 → **8000**（输出按实际产出计费 → 抬高上限几乎不增成本，只把「截断 + 次日重试」换成一次成功；两家 provider 实测均接受 ≤32768）；JSON 提取收敛到 **`extract_json_object()` 永不抛异常**；截断/不可解析统一抛 `BatchOutputError` 子类 → `analyze_batch` **自动对半降批重试**（拆到 1 条）；单条仍失败才走原「抛出 / 失败占位」语义，且失败占位**不经过 `_finalize`**（否则会被整条评论兜底匹配成「1 观点 + conf=0」，形状上不像失败 → 落库固化）；API/网络异常**不降批**（拆开照样失败，只会把 1 次失败放大成 N 次请求）。④**顺带量化的事实**：生产库长度分布下 **p90 批次总长 1215 字已落在观测到的截断区间（1092 字即炸）** → 现生产链路本身就贴近 2500 上限，不是实验偏差产物。⑤**模型对比结论（供后续决策，未切模型）**：情感判断**持平**（跨模型一致 95.2%，明显错各 1 条）；glm 标签更干净（同评论标签重复率 8.3/11.3% vs 16.7/16.3%）但稳定性略低（观点数一致率 81.0% vs 92.9%）；**glm 健壮性明显更好**（长评批次 0 失败 vs 8/10 条失败）；glm（最低档 `reasoning_effort=low`）比现生产 DeepSeek 慢 **2.5~3×**（1.43s/条 vs 0.52s/条）；62 条样本偏小，只算方向性结论。⑥**第三个洞一并收口（原计划的「观察项」按工程师选 A 落地）**：JSON 合法但**缺 index** / `results` 为空数组，原来静默返回 `_empty_result()` → 经 `_finalize` 的整条评论兜底匹配后落成「1 观点 + conf=0」的**假标注**（实测：「挂壁游戏外挂满天飞」→ 伪造出 `外挂与作弊现象` 观点、`_is_analysis_failure` 判 False → **会被落库固化**）。改：新增 `OutputIncomplete` 抛错 → 降批；**并顺势收紧 `raise_on_error` 口径为「系统性故障才抛」**——①API/网络/鉴权照旧抛；②`ok_total == 0`（整批一条都拿不到 = 真·故障夜）抛，保住 P1#1 的失败可见性；③单条调用输出不可用抛；**个别条答不动只跳过该条 + 告警**（若为单条抛错，`run_pipeline` 会让 target 每天失败、哨兵每天补采，而那条评论模型就是答不动 → **永久卡死**）。⑦**端到端实测验证**（真实 API，用上次炸掉的 20 条评论复现）：`max_tokens=2500` 下 DeepSeek `finish_reason=length` / `completion=2500` / 8413 字符——旧解析逻辑抛 `JSONDecodeError`（正是生产里那个未捕获异常），新代码**自动降批 10→5+5、10 条全部拿到观点、0 异常**；默认 8000 上限下 20 条一次过（**2 次请求、20/20 有观点、0.92s/条**，`finish_reason=stop`，completion **3067 / 2583** —— 直接证明 2500 确实不够）。pytest **271 → 289 例**（**288 passed / 1 skipped**）。⑧**主标注器切到 GLM-5.3-Flash、DeepSeek 降为备用**（工程师指令）：`.env` 改 `ANALYZER_PROVIDER=glm-5.3-flash` + `GLM_API_KEY=<用户变量 glm_plan>` + `GLM_5_3_FLASH_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4/`（**套餐 key 只能走 Coding 专属端点**，标准端点实测 429「余额不足或无可用资源包」）；备用 DeepSeek 的 `DEEPSEEK_MODEL` 由下线旧名 `deepseek-v4-flash` 改为官方现名 `deepseek-flash`（旧名仍可调但由 V4.1-Flash 供服务）。**切换实测**：走生产入口 `get_analyzer()` → `llm:glm-5.3-flash@73892f47`、8/8 有观点、0 失败占位。⚠️ **该次实测 8.46 s/条**（此前同口径中位 1.43 s/条）—— GLM 延迟方差显著大于 DeepSeek（早前见过 11.8~33.3 s/批与 170.9 s 单批尖峰），**需观察首个 02:00 实跑**再决定是否调批大小/并发。同步修正全部「主标注器 = deepseek」的过期说法：`.env.example`、`README.md`、`docs/guides/QUICK_START.md`、`docs/plan/DEVELOPMENT_PLAN.md`、`src/runtime_mode.py`、`src/analyzers/base.py`、`src/analyzers/sentiment_llm.py`（provider 注释）、`scripts/dev/verify_glm_5_3_flash.py`（**重写为验证生效配置**：走 `get_analyzer()`，断言 provider 与 coding 端点，`.env` 回切即失败）、`docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md` 与 `tests/test_p1_hardening.py`（**VPS 护栏论证**：改后本地 glm / VPS 回落 deepseek → `analyzer_version` 一眼可辨，但那只算"事后可识别"，**不是护栏**，结论不变）。⑨**标注并发（2026-10-01）**：切 GLM 后单请求延迟方差大，串行会让 02:00 链路更可能压进 03:00 哨兵窗口（**哨兵在「02:00 仍在运行」时整个跳过 → 那晚失败不补采**）。先实测再定：同一样本 30 条 —— `batch_size` 5 → 3.29 s/条、**10 → 2.15 s/条**、20 → 2.57 s/条且单请求 p50 从 21s 涨到 37.9s（**故批大小维持 10**）；并发 1 → 64.5s、2 → 48.4s、**3 → 23.5s（2.74×，0 失败）**。落地：`src/pipeline.py` 新增 `ANALYZER_CONCURRENCY`（默认 3，上限 8，非法值回退默认并告警）+ `_iter_chunk_results()`（**按窗口并发跑 LLM、按批顺序产出**，落库仍由主线程串行做 —— SQLite 写不可并发）+ `_persist_chunk_results()` 抽出复用；窗口内任一批抛异常仍向上抛（target 判失败），同窗口已发出请求的结果丢弃、下轮重试（幂等，只多花 token）。新增 11 例回归（env 解析 8 例参数化 + 真并发/设 1 串行/并发不串批 3 例） | 工程师选「B：先修上面那两个链路缺陷（含回归测试）」+「A：决定第三个洞怎么处理」+「切换模型，将 GLM 模型设为主模型，Deepseek 备用。然后整理提交」+「ABC 都做」（C = 观察/调整批大小与并发）；起因是「测试将原声标注链路模型改为 glm-5.3-flash 是否可行」+「max_token 如果容易撞上限，是否需要提高该参数？」 |
| 2026-09-21 | **对抗式审查报告全量收口（P1×2 + P2×3 + P3×4，新增 12 例回归）**：①**P1#1 数据完整性（最重）**——`src/pipeline.py` 原逐条 `analyze()` 默认吞异常，DeepSeek 超时/限流/400 时返回 `neutral/conf=0` 占位却仍被 `update_analysis` 固化 `analyzed_at`（**一个故障夜当晚评论永久标 neutral、永不重试、无告警**，且 `analyzer_version` 与正常标注一致故揪不出）。改：主链路**按批** `analyze_batch(..., raise_on_error=True)`（顺带吃到 10 条/批降本；`local`/Fake 分析器用**签名探测**规避 TypeError，不用 `except TypeError` 以免吞真异常）＋**失败占位（零置信度且无观点）跳过落库**（`analyzed_at` 留空 → 下轮重试）双保险；report 增 `analysis_skipped`。②**P1#2 输入收敛**——`/api/games/meta` 单次目标数截断 **≤8**；只服务 `steam:<digits>` 且 ∈ 监控白名单∪collect_tasks 者（原可任意 key 灌 `game_meta` 表）；`_refresh_game_meta`/`_download_cover` 加 appid `\d{1,12}` 白名单（**封堵 `steam:../../evil` 路径穿越写**）；后台刷新加 in-flight 去重（`_META_REFRESH_INFLIGHT`）。③**P2#1** 公开端点 `/api/targets`、`/api/bilibili/videos` **移除 `include_hidden`**（原任何访客加参数即可看到 admin 隐藏的目标/视频）；`data.js` 同步去参 + 缓存串 bump `data.js?v=20260921`。④**P2#3** `q` 搜索新增 `_escape_like()`（`%`/`_` 字面化，不再可放大全表扫描）。⑤**P2#5** `_LOGIN_FAILURES` 补 `_LOGIN_MAX_KEYS` 淘汰，并去掉「每次登录尝试都注册空 key」的无界增长根因。⑥**P3**：`Danmaku.to_dict` 从 `bucket_danmaku_rows` 的 `return` 之后（死代码、不属于任何类）**归位到类内**；词云 TF 缓存指纹由 `(count,max_id)` 扩为 `(+Σlength,min,max(content))`（`upsert` 覆盖既有正文也失效）；`update_analysis` 的 `valid_l1/l2_labels` 主链路接线（原休眠）；B站昵称脱敏措辞「无法反查」→「不可**直接**反查」（sha1 前 8 位 32 bit 可字典碰撞）。⑦**P2#4** Agent 提示注入残留写入 `ORIGINAL_VOICE_ANALYSIS_AGENT.md §7` 威胁模型（tool 全只读 + DOMPurify + 无外发 + 额度闸 → 风险上限「答错数据」）。⑧**P2#2**（明文 HTTP / 仓库内 VPS IP）经工程师决定**不处理**。pytest **259 → 271 passed / 0 failed**；审查报告为**一次性审查产物**，已按工程师要求在修复完成后**删除**（不随仓库留档，本条版本记录即修复档案） | 工程师「根据审查文档，优化代码」+「如果全部修改完成，REVIEW 文件可以删除」 |
| 2026-09-12 | **修掉「VPS 数据整天不完整」的调度缺口（哨兵补采回推 VPS）+ 更正「VPS 缺凭据」这一错误理由**：①**缺口**：推库在 02:00 采集链**末尾**（`--push-db`，`daily_incremental_collect.py` 步骤 5.5，实测 02:07:34「VPS 推送完成」），**不在 02:00 准点**；但 02:00 部分目标失败时推的是**部分快照**（推送发生在「判失败」之前），而 03:00 哨兵补采**不带 `--push-db`** → VPS 整天停在「缺几个目标」的中间态（**看起来像那几款游戏今天没有新评论**，比「数据旧」更误导）。实测 9/12：本地 19,043 / VPS 18,950，**差 93 条 = 当日补采量**（哨兵日志 `analyzed=93` 完全吻合）。②**修法（工程师选 A）**：`check_daily_collect.py::run_backfill` 命令加 `--push-db` —— 正常夜只推一次；失败夜 02:07 推部分 + 03:0x 推完整 → **VPS 1 小时内自愈**；推送非阻塞不污染哨兵判定；新增测试 `test_backfill_carries_push_db_flag` 锁住 flag。**残余**：02:00 采集成功但推送本身失败时，哨兵判「成功」→ 跳过 → 那天 VPS 停在旧数据（仅一行 warning）。**不选 B（整体搬到哨兵后）**：哨兵在「02:00 仍在运行」时会整个跳过 → 那晚不推库。③**更正一处错误理由**：「VPS 无生产标注器 Key → 回落 deepseek → 写脏 `analyzer_version`」**不成立** —— VPS `.env` **有** `DEEPSEEK_API_KEY`（Agent 对话必需）、**无** `ANALYZER_PROVIDER` → 取默认 deepseek、**能真标注**；本地生产标注器也是 deepseek → `analyzer_version` 口径一致、**看不出异常**（旧说法只在生产标注器是 GLM 的 2026-08-31~09-08 成立）。已改 `runtime_mode.py` / `SELF_HOSTED_VPS_DEPLOYMENT.md` / `.env.example` / `tests/test_p1_hardening.py`；同批核实 VPS **无** `BILIBILI_SESSDATA`、**无** torch（该两条理由成立）。④**实测 VPS 无任何采集调度**：crontab / cron.d / systemd timers 全无 VoC 条目、`grep -rn daily_incremental_collect /etc/cron* /etc/systemd` 为空（脚本在但没人调用）；写操作 401/200/403 三态实测符合预期。⑤**手动推库一次把 VPS 追平**：`live_comments_before=18950 → after=19043`、SHA256 双向一致、`live_integrity=ok`、原位 `.backup()` 不重启且不影响读。⑥pytest **258 passed / 1 skipped**、死引用扫描 0 | 工程师「1. A 2. 不做 3. 改 另外，推一次最新数据到VPS」 |
