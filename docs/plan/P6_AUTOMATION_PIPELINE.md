# P6 · 自动化采集流水线 — 设计与落地计划

> **目标**：让 GitHub Actions 每天定时跑一次采集 + 分析，并把结果**累积成可下载的单库**，解锁时间序列趋势（P8）、监控形态、长期口碑对比。
>
> **关联文档**：
> - 路线图：[DEVELOPMENT_PLAN.md §四 / §六](./DEVELOPMENT_PLAN.md)
> - 标注流程：[ANNOTATION_PIPELINE.md](../architecture/ANNOTATION_PIPELINE.md)
> - 存储设计：[DATA_STORAGE_DESIGN.md](../architecture/DATA_STORAGE_DESIGN.md)
> - GitHub Actions 现状：[.github/workflows/daily-collect.yml](../../.github/workflows/daily-collect.yml)
>
> **最后更新**：2026-08-19
> **状态**：📝 计划阶段（待工程师确认后开工）

---

## 0. 一句话总览

**把"每天跑一次"变成"每天累一次"。** 当前 workflow 每天生成一个新库就丢；目标是让一份累积 DB 在 GH Release 里持续生长，仪表盘与时间序列都能用同一份数据。

---

## 1. 现状诊断（为什么 P6 至今没真正落地）

### 1.1 workflow 跑得到但留不下

```yaml
# 当前 .github/workflows/daily-collect.yml 关键节选
- uses: actions/checkout@v4   # 每个 run 的工作目录是全新的
- run: python -m src.pipeline --platform steam --target 730 --count 50
- uses: actions/upload-artifact@v4
  with: { path: data/voc.db, retention-days: 30 }
```

**问题**：

| 问题 | 后果 |
|---|---|
| `actions/checkout@v4` 没有持久化上一次的 DB | 每个 run 起步是空 DB，新采 50 条覆盖前 49 天 |
| Artifact 保留 30 天后自动清掉 | 30 天后无任何历史数据可回看 |
| Artifact 只在 GitHub UI 下载，不在代码里 | 仪表盘/CI 拿不到 |
| 单目标（CS2/730）、固定 50 条 | 不足以体现"趋势" |
| 没区分"采集已存在评论" vs "新评论" | 重复采集 + analyzer 重复调用，浪费 token |

### 1.2 现有可复用基础设施（**非必要不新增**的核心依据）

| 能力 | 位置 | 说明 |
|---|---|---|
| `bulk_upsert` 按 `(platform, source_id)` 去重 | `src/storage/db.py` | 同一条评论跨天再采 → 不会重复入库 |
| 跳过已分析评论 | `src/pipeline.py` 步骤 4 | `if c.analyzed_at is not None: continue` —— analyzer 不会被重复调用 |
| 增量向量化 | `embedder.find_missing_embedding_ids` | 只对缺向量的评论编码；换模型已三防线 |
| 应用层时间窗过滤 | `--posted-after / --posted-before` | 不依赖 Steam API 时间窗语义 |
| 7 天后回采 likes | `scripts/ops/refresh_likes.py` | 独立脚本，独立 cron |
| 数据库 schema | `comments` + `comment_opinions` + `comment_embeddings` + `danmaku` | 不动 schema |

> ✅ 增量入库的"原子能力"全部就位，缺的只是 **workflow 编排 + 跨 run DB 持久化 + 多目标驱动**。

### 1.3 P6 vs P9 阶段 0 关系

| | P6 · 自动化流水线 | P9 · 阶段 0 时序持久化 |
|---|---|---|
| 解决什么 | "每天跑一次并留下来" | "留下来后能查时间序列" |
| 关键交付 | workflow + 累积 DB + 多目标 | P8 趋势图查询 |
| 谁依赖 | P9 阶段 0 | P8 趋势图 |
| 状态 | **待开工** | 待 P6 完成后开工 |

> **本计划（P6）完成后立即解锁 P9 阶段 0 与 P8**，无需等 P9 阶段 1+ 收口。

---

## 2. 核心设计决策

### 2.1 决策 D1：跨 run DB 持久化方式 = GitHub Release asset

| 候选 | 评价 | 选 |
|---|---|---|
| GH Release asset（`voc-daily.db`） | 长期保留（不自动过期）、公开可下载、有版本号、可通过 `gh release download` 命令行拉 | ✅ |
| GH Actions artifact | 30 天自动过期、不可编程获取 | ❌ |
| 推到 git 仓库 | `.gitignore` 禁 DB；DB 上 git 会污染仓库 | ❌ |
| 自托管对象存储 | 个人项目过度工程化 | ❌ |

**机制**：
- 每天 00:00 UTC workflow 运行结束 → 创建一个新的 GitHub Release（tag `voc-daily-YYYY-MM-DD`），把 `data/voc.db` 作为 asset 上传。
- 第二天跑前：用 `gh release download voc-daily` 拉取上一份 → 把它作为"基础库" → 跑增量采集 → 再上传新一份。
- **本地手动同步**也用同一脚本：可指定 `--no-upload` 只本地落库，方便调试。

### 2.2 决策 D2：多目标驱动 = `config/monitoring/targets.yaml`

新加一个配置文件管理监控目标，复用现有 `config/topics/*.yaml` 模式（**业务知识与代码解耦**）：

```yaml
# config/monitoring/targets.yaml（计划）
version: 1
# 每个目标：平台 + ID + 语言 + 采集上限
# 启动时间窗 = 当前 max(posted_at) - 1 天滑窗（脚本里计算）
targets:
  - platform: steam
    id: "730"
    language: schinese
    count: 50
    enabled: true
  - platform: steam
    id: "1245620"
    language: schinese
    count: 30
    enabled: true
  - platform: bilibili
    id: "BV1UpwaeNESx"
    language: zh
    count: 200
    enabled: true
```

**为什么独立文件**：未来加新游戏/视频只需改 YAML，不必改 workflow 或脚本。

### 2.3 决策 D3：workflow 编排用 Python 入口，YAML 只负责环境

workflow 本身只做：checkout → Python 环境 → 装依赖 → 跑一个 Python 脚本。所有"下载上一天 DB / 跑多个 target / 上传 release" 都进 `scripts/ops/daily_incremental_collect.py`：

```
workflow YAML (薄)              Python 入口 (逻辑)
─────────────────              ──────────────────
┌─checkout─┐                   ┌──────────────────┐
│ env      │                   │ 1. gh release    │
│ setup-py │ ────────►         │    download      │
│ run:     │                   │ 2. 加载 YAML    │
│   python │                   │ 3. 对每个 target│
│   daily  │                   │    --posted-after│
│   _incr… │                   │    = max(ts)-1d │
└──────────┘                   │ 4. gh release    │
                               │    upload        │
                               └──────────────────┘
```

**理由**：Python 可测试、可本地复现；YAML 调试体验差。

### 2.4 决策 D4：增量语义 = 时间窗 + DB 去重

| 维度 | 策略 |
|---|---|
| 采集目标 | 监控列表（targets.yaml 中 `enabled: true`） |
| 时间窗 | `posted_after = max(posted_at) - 1 天滑窗`（避免边界漏采） |
| 去重 | 复用 `bulk_upsert` 的 `(platform, source_id)` 唯一键 |
| 分析 | 复用 `if c.analyzed_at is not None: continue` 跳过 |
| 向量化 | 复用 `find_missing_embedding_ids` 增量 |
| 回采 likes | **独立** workflow（`refresh_likes.yml`），7 天后才回，不混入每日 |

### 2.5 决策 D5：失败处理 = 幂等 + 通知

- 单个 target 失败不阻塞后续 target（`try/except` 包裹，继续下一个）。
- workflow 末尾 step 上传 `$GITHUB_STEP_SUMMARY`：今日新增/累计/失败列表。
- 若上传 release 失败：发 `warning` 而非 `error`（第二天仍能从更新后的本地库继续）。

---

## 3. 交付物清单

> ⚠️ 全部按 `AGENTS.md §1/§2/§4` 过三关后才纳入。每个文件必须**非必要不新增**或**必要新增且登记**。

| # | 文件 | 类型 | 必要性论证 |
|---|---|---|---|
| D1 | `config/monitoring/targets.yaml` | 新建 | 监控目标管理；不复用既有文件因为这是新维度（既有 config 是 prompt/标签） |
| D2 | `scripts/ops/daily_incremental_collect.py` | 新建 | workflow 编排入口；放入 `scripts/ops/`（长期运维） |
| D3 | `tests/test_daily_incremental_collect.py` | 新建 | AGENTS.md §3 红线："测试/验证必须独立"；增量语义不能没有测试 |
| D4 | `.github/workflows/daily-collect.yml` | **改** | 现有 workflow；改为薄编排调用 D2 |
| D5 | `config/README.md` | **改** | AGENTS.md §4："配置改动登记版本记录"；新增 `monitoring/` 子目录 |
| D6 | `scripts/README.md` | **改** | AGENTS.md §4："新脚本必须登记"；D2 必须登记 |
| D7 | `docs/00-index.md` | **改** | AGENTS.md §4："新文档必须登记"；本文件 + 未来监控相关 |
| D8 | `docs/architecture/AUTOMATION_PIPELINE.md` | 新建 | 架构文档（与 `BILIBILI_COLLECTION.md` 同级）；解释 GH Release 选择 + 失败语义，作品集归档用 |
| — | `src/pipeline.py` | **不改** | 增量语义已具备；只通过 `--posted-after` 触发 |
| — | `src/storage/db.py` | **不改** | schema 与去重规则已支持 |
| — | `src/collectors/*.py` | **不改** | 无需改采集器 |

> **新增总计 4 个文件 + 改 4 个文件**。如需进一步精简，可考虑 D1 与 D2 的拆分是否合理（详见 §6.2 风险）。

---

## 4. 验收标准（怎么算"完成"）

### 4.1 功能验收

- [ ] 本地连续 3 天模拟运行（`python scripts/ops/daily_incremental_collect.py --no-upload`）：
  - 第 1 天：从空库起步，采集目标清单全部 enabled target
  - 第 2 天：从第 1 天的本地库起步，仅采集新评论（`posted_after` 滑窗生效）
  - 第 3 天：评论总量稳定（不应无限增长），新增评论 = 实际新增
- [ ] workflow 在 GitHub Actions 上跑通：
  - 步骤摘要显示"今日新增 N 条 / 累计 M 条 / 失败 0"
  - Release `voc-daily-YYYY-MM-DD` 创建成功，asset `voc.db` 可下载
  - 第二天 workflow 自动拉取前一天的 DB 后跑增量
- [ ] 跨 run DB 一致性：
  - 从 GH Release 下载 DB 后，本地 Streamlit 直接打开（`data/voc.db` 路径不变）
  - `comment_embeddings` 不重复（`comment_embeddings.comment_id` 唯一）
  - `comment_opinions` 不重复（同一 comment_id + full_path 不重复）

### 4.2 测试验收（AGENTS.md §3 红线）

- [ ] `pytest tests/test_daily_incremental_collect.py -v` 全绿
- [ ] 至少 4 个用例：
  - 空库起步 → 采集后 DB 写入正确
  - 有库起步 → 只新增（不覆盖已有 likes_refreshed_at）
  - 时间窗计算正确（`max(posted_at) - 1 天`）
  - 单 target 失败不阻塞其他 target

### 4.3 护栏验收

- [ ] `scripts/smoke_test.py` 仍通过
- [ ] `pytest tests/` 全绿（既有测试不退化）
- [ ] AGENTS.md §6 健康检查清单 7 条全过

---

## 5. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| GH Release asset 体积上限（2 GB 单文件） | 🟢 | 当前 DB < 50 MB，3 年累积 < 500 MB，留 4x 安全冗余；超限再切 GH Packages 或对象存储 |
| `GITHUB_TOKEN` 默认权限是否含 `contents: write` | 🟡 | workflow 默认 token 已含此权限；如改为 fine-grained PAT 需显式开 |
| PAT `workflow` scope 阻塞 workflow 文件推送 | 🟡 | 与本任务无关；daily run 不涉及 workflow 文件变更；详见 plan §六 |
| CI 主机无 ML 环境（sentence-transformers/torch） | 🟢 | embedder 自动降级（`embedder is None` 跳过）；本地手动 `--upload` 可补 |
| 单 target 失败（如 Steam 风控）影响当天其他 target | 🟢 | try/except 包裹，单个失败仅记 warning 不中断 |
| `voc-daily` release 不存在（首次跑） | 🟢 | 脚本首跑识别 → 直接当作空库起步 |
| DB 增长快导致 workflow 30 分钟超时 | 🟢 | 当前流水线 < 5 分钟（CS2 50 条）；10 目标/天仍 < 20 分钟；超时再分批 |
| 现有本地 `data/voc.db` 与首次 workflow 跑出来的 DB 数据集差异 | 🟢 | **首次跑前**本地备份现有库为 `voc.db.bootstrap.bak` 并手动创建一个"基线 release" `voc-daily-bootstrap`，workflow 从第二跑开始自动接续 |
| CS2 (730) 补采 0 新增 | 🟡 | **本任务不修**；P6 落地后此问题可单独排查（见 plan §六 CS2 行） |

---

## 6. 实施步骤（按依赖顺序）

> 每步独立可回滚；建议每步独立 commit + 在本地"模拟一日跑"。

### 步骤 0 · 准备（半人工）

- [ ] 把当前本地 `data/voc.db` 备份为 `data/voc.db.bootstrap.bak`
- [ ] 手动创建 GH Release `voc-daily-bootstrap`（tag），上传 `voc.db.bootstrap.bak` 重命名为 `voc.db` 作 asset
- [ ] 在仓库 Settings → Secrets 确认 `STEAM_API_KEY`、`DEEPSEEK_API_KEY` 已配（已有）

### 步骤 1 · 配置层（D1 + D5）

- [ ] 新建 `config/monitoring/targets.yaml`：先把现有 10 款 Steam + 1 条 B 站填进去，默认 enabled
- [ ] 更新 `config/README.md`：
  - 目录树加 `monitoring/` 一栏
  - 版本记录追加一行（2026-08-19，新建 targets.yaml）

### 步骤 2 · Python 入口（D2）

- [ ] 新建 `scripts/ops/daily_incremental_collect.py`：
  - `--release-tag` 默认 `voc-daily`
  - `--no-upload` 仅本地（调试用）
  - `--targets-config` 默认 `config/monitoring/targets.yaml`
  - 流程：拉 DB → 加载 YAML → 计算 posted_after → 对每个 enabled target 调 `run_pipeline` → 汇总 → 上传 release
  - 跨 target 容错：单失败 try/except 不中断
  - 写 $GITHUB_STEP_SUMMARY 摘要
- [ ] 注册到 `scripts/README.md`：新增「自动化编排」一节；版本记录追加

### 步骤 3 · 测试（D3）

- [ ] 新建 `tests/test_daily_incremental_collect.py`：4 个用例（见 §4.2）
- [ ] 用本地临时 DB（不污染 `data/voc.db`），符合 §3 红线

### 步骤 4 · Workflow 编排（D4）

- [ ] 重写 `.github/workflows/daily-collect.yml`：
  - 移除 inline `python -m src.pipeline` 调用
  - 改为 `python scripts/ops/daily_incremental_collect.py`
  - 默认 token `permissions: contents: write`（GH Release 上传需要）
  - 步骤摘要 `$GITHUB_STEP_SUMMARY` 由 Python 脚本输出
  - artifact 上传**保留作为 fallback**（即使 release 失败，仍有 30 天窗口数据）

### 步骤 5 · 文档（D7 + D8）

- [ ] 新建 `docs/architecture/AUTOMATION_PIPELINE.md`：
  - 流程图（workbuddy ASCII）
  - GH Release 选择理由
  - 失败语义
  - 本地手动同步指引（`--no-upload`）
- [ ] 更新 `docs/00-index.md`：在 `architecture/` 子目录树里加一行；"我想知道…" 表加一条

### 步骤 6 · 联调

- [ ] 本地完整模拟：连续 3 天每日跑一次（间隔无要求，DB 演进正确即可）
- [ ] push 到分支 → 在 GitHub 上 `workflow_dispatch` 手动触发一次
- [ ] 验证 GH Release 是否创建、asset 是否下载回本地能用
- [ ] 第二天观察定时跑自动拉取前一天的 release

### 步骤 7 · 收口

- [ ] AGENTS.md §6 健康检查 7 条全过
- [ ] 更新 `docs/plan/DEVELOPMENT_PLAN.md`：
  - §四 P6 状态：✅ 已完成（自动化落地）
  - §六 P6/P9 阶段 0 阻塞：移除
  - §三 数据快照补一行："GH Release `voc-daily` 持续累积"
  - §八 M10 状态：✅ 完成
- [ ] 跑一次 `python -m src.pipeline ...` 本地烟雾测试
- [ ] commit + push（注意：workflow 文件改动若需 `workflow` scope 时改网页端）

---

## 7. 不做的事（防止 scope creep）

> 这些**不在本计划范围内**，避免 P6 变 god module。

- ❌ 修改 `src/pipeline.py` 的结构或接口
- ❌ 修改数据库 schema
- ❌ 修改采集器
- ❌ 修 CS2 补采 0 新增（独立任务）
- ❌ 新增 dashboard 视图
- ❌ 新增跨 run 去重的"评论文本相似度"逻辑（DB 唯一键足够）
- ❌ 把 .env 加密/secrets 增强（既有的 GH Secrets 已足够）
- ❌ L3.5 聚类 / PEDM 试点（属 P9 阶段 2/3，独立计划）
- ❌ 微博 / 小红书接入（属 P7，独立计划）

---

## 8. 与既有约定的一致性自检

按 `AGENTS.md §1-§6` 逐项核验：

- [x] §0 非必要不新增：4 个新文件 + 4 个改动，逐一论证必要性（§3）
- [x] §1 归属：脚本进 `scripts/ops/`、配置进 `config/`、测试进 `tests/`、文档进 `docs/architecture/`
- [x] §2 命名：snake_case 文件；Python 类 CamelCase（待实现时遵守）；无禁名
- [x] §3 红线：测试用独立临时 DB（不污染 `data/voc.db`）；新脚本从项目根可运行
- [x] §4 登记：D5/D6/D7 三处 README/index 同步更新已列入实施步骤
- [x] §5 记忆：交付后同步 `.workbuddy/memory/2026-08-19.md`（仅备忘，不放规范）
- [x] §6 健康检查：步骤 7 列入清单

---

## 9. 决策待工程师确认

> 这些是真正需要你拍板的；其他实现细节我可直接开工。

| # | 决策 | 我的建议 |
|---|---|---|
| Q1 | DB 累积方式是否同意采用 **GH Release asset**？ | ✅ 推荐，标准做法；自托管/对象存储都过度 |
| Q2 | 监控目标是否先用现有 **10 款 Steam + 1 条 B 站**起步？ | ✅ 推荐；后续按需扩 |
| Q3 | workflow 是否保留 **artifact 上传作为 fallback**？ | ✅ 推荐；失败时仍留 30 天窗口 |
| Q4 | 首次跑前是否同意手动建 **voc-daily-bootstrap** release？ | ✅ 推荐；否则首跑从空库起步丢失 9308 条历史 |
| Q5 | 回采 likes（7 天）是否合并入 daily workflow？ | ❌ **建议独立**；频次/语义不同，混入会增加复杂度 |

---

## 📋 版本记录

| 更新时间 | 内容 | 原因 |
|---|---|---|
| 2026-08-19 | 初版：诊断 + 设计决策 + 交付物 + 验收 + 实施步骤 | P6 自动化流水线规划落地 |