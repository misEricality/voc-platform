# AGENTS.md — 项目工程约定（每次会话必读）

> **这份文件是代理（Agent）与工程师共同遵守的唯一工程规范来源。**
> 每次新会话开始时，代理必须先读完本文件再动手，无需工程师重复强调。
>
> **最后更新**：2026-08-19

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
│   ├── analyzers/            分析器（情感 / 语义 / 标注）
│   ├── collectors/           采集器（steam / bilibili ...）
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

> 文档允许用中文名（如 `新标签体系验证报告.md`），但须自解释、与目录职责一致；能英文 snake 名尽量英文。

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

---

## 📋 版本记录

| 更新时间 | 内容 | 原因 |
|---|---|---|
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
