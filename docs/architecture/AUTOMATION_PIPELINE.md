# 自动化采集流水线（P6 · 落地架构）

> **用途**：让任何工程师/产品/评审同事能 30 秒内理解"每天 00:00 UTC 自动化采集是怎么跑的、数据怎么累积"。
>
> **关联文档**：
> - 路线图总纲：[plan/DEVELOPMENT_PLAN.md](../plan/DEVELOPMENT_PLAN.md)
> - 主采集器：[pipeline.py](../../src/pipeline.py) + [steam.py](../../src/collectors/steam.py)
> - 存储层：[DATA_STORAGE_DESIGN.md](./DATA_STORAGE_DESIGN.md)
> - P6 决策与风险历史：本文档 §8（2026-08-22 从 `plan/P6_AUTOMATION_PIPELINE.md` 合并，原文件已删）
>
> **最后更新**：2026-08-27
> **状态**：✅ 已落地（2026-08-19）；2026-08-22 洁癖收口：合并 `plan/P6_AUTOMATION_PIPELINE.md` 决策与风险历史 → §8；2026-08-27 §8.3 A1 升级：silent 失败实战案例 + verify_release_upload.py 防御上线（详见版本记录）

---

## 0. 一句话总览

GitHub Actions 每天 UTC 00:00 调 `scripts/ops/daily_incremental_collect.py`，把"前一天累积的 DB"从 GitHub Release 拉下来 → 对 6 款 Steam 单机游戏做增量采集 → 把更新后的 DB 上传回 GitHub Release。**每天的 DB 是同一份累积库**，不再每天生成新库。

---

## 1. 端到端流程图

```
┌─────────────────────── GitHub Actions (UTC 00:00) ───────────────────────┐
│                                                                          │
│   ┌─checkout─┐                                                           │
│   │ setup-py │                                                           │
│   │ install  │                                                           │
│   └────┬─────┘                                                           │
│        │                                                                 │
│        ▼                                                                 │
│   python scripts/ops/daily_incremental_collect.py                       │
│        │                                                                 │
│        ▼                                                                 │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ 1. 拉前一天 release voc-daily-YYYY-MM-DD 的 voc.db asset       │   │
│   │    ├─ 找到 → 下载到 data/voc.db（原子替换）                    │   │
│   │    └─ 找不到 → 回退 voc-daily-bootstrap（基线）               │   │
│   │              └─ 仍找不到 → 空库起步                           │   │
│   │                                                                 │   │
│   │ 2. 加载 config/monitoring/targets.yaml（6 款 Steam）         │   │
│   │                                                                 │   │
│   │ 3. 对每个 enabled=true 的目标：                                │   │
│   │    posted_after = max(posted_at) - 1 天 滑窗                   │   │
│   │    → run_pipeline（依赖 bulk_upsert 去重 + analyzed_at 跳过） │   │
│   │    → 单 target 失败 try/except 不中断后续                      │   │
│   │                                                                 │   │
│   │ 4. 写 $GITHUB_STEP_SUMMARY（今日新增 / 累计 / 失败列表）       │   │
│   │                                                                 │   │
│   │ 5. 上传 data/voc.db → 创建今日 release voc-daily-YYYY-MM-DD   │   │
│   │    └─ 失败仅记 warning，下一次跑仍能从本地 DB 继续            │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│        │                                                                 │
│        ▼                                                                 │
│   ┌─artifact upload─┐  （fallback，保留 30 天 UI 可下载）              │
│   └─────────────────┘                                                   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 关键设计决策（5 条）

### 2.1 为什么用 GitHub Release 而不是 artifact / git / 自托管

| 候选 | 评价 | 选 |
|---|---|---|
| **GitHub Release asset** | 长期保留（不自动过期）、公开可下载、有版本号、可命令行拉（`gh release download`） | ✅ |
| GH Actions artifact | 30 天自动过期、不可编程获取 | ❌ |
| 推到 git | `.gitignore` 禁 DB；DB 上 git 会污染仓库 | ❌ |
| 自托管对象存储（S3/OSS） | 个人项目过度工程化 | ❌ |

### 2.2 为什么多目标用 YAML 而不是硬编码

- `config/monitoring/targets.yaml` 是**业务配置**（目标清单），不是代码逻辑。
- 新增/删除监控游戏 = 改 YAML 一行，无需改 Python/workflow。
- 与既有 `config/topics/*.yaml`（标签）、`config/prompts/*.txt`（prompt）一致。

### 2.3 为什么 workflow YAML 只做环境、业务逻辑进脚本

- workflow YAML 调试体验差（语法严格、本地难复现）。
- Python 可测试（`tests/test_daily_incremental_collect.py` 6 个用例全绿），可本地复现（`--no-download --no-upload`）。
- workflow 唯一业务决定：cron + 调用哪个 Python 脚本。

### 2.4 增量语义 = 滑窗 + DB 去重（v2：北京日历日 + 当天严格不采）

- **采集时间窗（v2，2026-08-31 升级）**：以**北京日历日**为准，每天采集「**北京昨天全天 + 北京前天全天**」，**当天严格不采**。
  - `posted_before = 北京当天 0:00 UTC 表示`（= UTC T-1 16:00），由 `_passes_time_filter` 卡死当天边界（`ts >= posted_before` 返回 False）
  - `posted_after = max(target_posted_after, 北京前天 0:00 UTC 表示)`，floor 兜底补救（昨天 workflow 失败时扩到前天）
  - `target_posted_after = max(posted_at) - 1 day`（保留原 calc_posted_after 行为，复用以做增量去重）
  - 实现：`scripts/ops/daily_incremental_collect.py::smart_window()`（30 行），整批 target 共享同一 `now_utc` 保证基准一致
- **采集时间窗（v1，已弃用）**：`posted_after = max(posted_at) - 1 天`，无 `posted_before` —— 导致每天有 10.9% 当天数据混入（2026-08-27 audit 发现）。
- **去重**：复用 `bulk_upsert` 的 `(platform, source_id)` 唯一键（已有机制，不引入新逻辑）。
- **分析去重**：复用 `if c.analyzed_at is not None: continue`（已有机制）。
- **向量化去重**：复用 `find_missing_embedding_ids`（已有机制；CI 环境无 ML 模型时自动降级跳过）。

### 2.5 失败语义

- **单 target 失败**（如 Steam 风控、单游戏接口挂）：`try/except` 包裹，仅记 warning，不影响后续 target。
- **release 上传失败**：仅记 warning，下次跑仍能从本地 `data/voc.db` 继续（不会丢数据）。
- **release 不存在**（首次跑或新克隆）：直接以空库起步；建议在 P6 落地时手工建 `voc-daily-bootstrap` 基线 release 保留历史。

---

## 3. 文件清单

| 文件 | 角色 |
|---|---|
| `.github/workflows/daily-collect.yml` | cron + setup + 调用 Python 入口 + artifact fallback |
| `scripts/ops/daily_incremental_collect.py` | 主入口：拉/推 release + 跑各 target + 写摘要 |
| `config/monitoring/targets.yaml` | 监控目标清单（6 款 Steam 单机游戏） |
| `tests/test_daily_incremental_collect.py` | 10 个回归用例（空库起步/时间窗/不擦旧数据/单失败容错/gh 容错/smart_window 4 场景） |
| `docs/architecture/AUTOMATION_PIPELINE.md §8` | P6 决策与风险历史（原 `plan/P6_AUTOMATION_PIPELINE.md` 已合并删除） |

---

## 4. 验收（已通过 2026-08-19，2026-08-31 v2 升级重测通过）

- [x] `pytest tests/test_daily_incremental_collect.py` 全 10 用例绿（v1=6 用例，v2 新增 4 个 smart_window 场景）
- [x] `pytest tests/` 全绿（含既有测试不退化）
- [x] `scripts/smoke_test.py` 通过
- [x] AGENTS.md §6 健康检查 7 条（按 §6 自检）

---

## 5. 本地手动同步（调试 / 离线补采）

```bash
# 跑一次完整流程（无 release 下载/上传，纯本地）
python scripts/ops/daily_incremental_collect.py --no-download --no-upload

# 强制全量重采（无视 posted_after 滑窗，DB 仍按唯一键去重）
python scripts/ops/daily_incremental_collect.py --no-download --no-upload --full-replay

# 调试用 targets.yaml 替换
python scripts/ops/daily_incremental_collect.py --no-download --no-upload \
    --targets-config path/to/other-targets.yaml
```

---

## 6. 不在自动化范围的事

- ❌ B 站采集：B 站为"发布满 7 天的稳态快照"（详见 [BILIBILI_COLLECTION.md](./BILIBILI_COLLECTION.md)），不属于每日采集；手动跑 `python -m src.pipeline --platform bilibili --target <bvid>` 触发。
- ❌ `refresh_likes` 回采：独立 workflow（频次/语义不同），由 P4 文档化的 `scripts/ops/refresh_likes.py` 处理；如未独立排期，临时合入 daily workflow 不推荐。
- ❌ L3.5 聚类 / PEDM 试点：属 P9 阶段 2/3，独立计划。
- ❌ 微博 / 小红书：属 P7。

---

## 7. 故障排查速查表

| 现象 | 可能原因 | 处理 |
|---|---|---|
| workflow 跑但 DB 没增长 | `--posted-after` 计算错（目标无 max(posted_at) → None）；或 release 没下载成功 | 查 `$GITHUB_STEP_SUMMARY` 是否抓到 `voc-daily-YYYY-MM-DD` |
| 向量化被跳过 | CI 主机无 `sentence-transformers` | 这是预期的；embedder 自动降级；本地手动同步可补 |
| 单 target 失败影响其他 | 旧版代码未包 `try/except` | 升级到 P6 落地版（`scripts/ops/daily_incremental_collect.py` 已包裹） |
| release 上传 403 | PAT/fine-grained token 无 `contents: write` | workflow 默认 `GITHUB_TOKEN` 已含；若改成 PAT 需显式开 |
| `data/voc.db` 在仓库中 | `.gitignore` 漏配 | 检查 `.gitignore` 含 `*.db`（已配） |
| release 存在但 `assets` 为空 | `gh release create --generate-notes` 对已存在 release（含手工 draft）有副作用导致 upload 落空 | 见 §8 「A1 已知问题」一节；建议「先 view 再 create」改造 |

---

## 8. 决策与风险历史

> 本节是 P6 计划阶段的设计决策、风险评估与已知问题的归档。2026-08-22 洁癖收口时从 `docs/plan/P6_AUTOMATION_PIPELINE.md`（已删除）合并而来。
>
> **当前决策的现役答案仍以本文档 §2 为准；本节用于回答「当时为什么这么选」和「潜在风险清单」。**

### 8.1 五条核心决策回顾

| # | 决策 | 当初候选 | 当时选择 | 现役位置 |
|---|---|---|---|---|
| D1 | 跨 run DB 持久化方式 | GH Release asset / GH artifact / git push / 自托管对象存储 | GH Release asset（长期保留 + 公开可下载 + 命令行可拉） | §2.1 |
| D2 | 多目标驱动 | 硬编码 vs YAML | `config/monitoring/targets.yaml`（业务配置与代码解耦） | §2.2 |
| D3 | workflow 编排 | 全部进 YAML vs Python 入口 + 薄 YAML | Python 入口（可测试 + 可本地复现） | §2.3 |
| D4 | 增量语义 | 全量 vs 时间窗 vs DB 去重 | **v2**：以北京日历日为准 + `smart_window()` 算 posted_after/Before（floor = 北京前天 0:00 补救；当天严格不采）；复用 `bulk_upsert` 去重 + 复用分析/向量化跳过<br>**v1**（已弃用）：`posted_after = max(posted_at) - 1 天`，无 posted_before | §2.4 |
| D5 | 失败处理 | 任一失败中断 vs 全部跳过 vs 单点隔离 | 单 target try/except 隔离 + release 失败仅 warning（不丢数据） | §2.5 |

D1 候选详细对比（plan §2.1 原表，已与 §2.1 合并确认）：

| 候选 | 评价 | 选 |
|---|---|---|
| GH Release asset（`voc-daily.db`） | 长期保留（不自动过期）、公开可下载、有版本号、可通过 `gh release download` 命令行拉 | ✅ |
| GH Actions artifact | 30 天自动过期、不可编程获取 | ❌ |
| 推到 git 仓库 | `.gitignore` 禁 DB；DB 上 git 会污染仓库 | ❌ |
| 自托管对象存储 | 个人项目过度工程化 | ❌ |

### 8.2 风险与缓解（plan §5 原表，已评审 2026-08-19）

| 风险 | 等级（2026-08-19） | 缓解 |
|---|---|---|
| GH Release asset 体积上限（2 GB 单文件） | 🟢 | 当前 DB < 50 MB，3 年累积 < 500 MB，留 4x 安全冗余；超限再切 GH Packages 或对象存储 |
| `GITHUB_TOKEN` 默认权限是否含 `contents: write` | 🟡 | workflow 默认 token 已含此权限；如改为 fine-grained PAT 需显式开 |
| PAT `workflow` scope 阻塞 workflow 文件推送 | 🟡 | 与 daily run 无关；仅影响 workflow 文件变更；详见 DEVELOPMENT_PLAN §六 |
| CI 主机无 ML 环境（sentence-transformers/torch） | 🟢 | embedder 自动降级（`embedder is None` 跳过）；本地手动 `--upload` 可补 |
| 单 target 失败（如 Steam 风控）影响当天其他 target | 🟢 | try/except 包裹，单个失败仅记 warning 不中断 |
| `voc-daily` release 不存在（首次跑） | 🟢 | 脚本首跑识别 → 直接当作空库起步 |
| DB 增长快导致 workflow 30 分钟超时 | 🟢 | 当前流水线 < 5 分钟（CS2 50 条）；10 目标/天仍 < 20 分钟；超时再分批 |
| 现有本地 `data/voc.db` 与首次 workflow 跑出来的 DB 数据集差异 | 🟢 | **首次跑前**本地备份现有库为 `voc.db.bootstrap.bak` 并手动创建「基线 release」`voc-daily-bootstrap`，workflow 从第二跑开始自动接续 |
| CS2 (730) 补采 0 新增 | 🟡 | P6 落地后此问题可单独排查（见 DEVELOPMENT_PLAN §六 CS2 行） |

### 8.3 A1 已知问题（2026-08-22 洁癖收口新增，2026-08-27 升级）

> ⚠️ **P6 落地后实际运行发现**：GH Release 每日创建成功但 `assets: []`（voc.db 未上传）——「累积 DB」核心目标实际未生效。

**根因**（静态分析见 `.workbuddy/memory/2026-08-22.md` A1 节）：

1. `scripts/ops/daily_incremental_collect.py:165-195` 的 `gh_release_upload` 是「先 create 再 upload」
2. 当 release **已存在**（哪怕是用户手工 draft）时，`gh release create --generate-notes` 报错但被静默忽略；同时 **副作用**：旧 release 被自动 publish
3. 紧接着的 `gh release upload` 在 release 处于「刚被 publish 的瞬态」时失败
4. 脚本仅打 warning，workflow 仍标 success → 远端 artifact 正常上传但 release asset 为空

**沉默失败真实案例**（2026-08-27 audit）：

工程师中午手动 dispatch #29（run 14m34s，UI 显示 ✅ success），5h 后延迟 cron #30（run 16m53s，UI ✅ success）。
sync 当天 release 后查 DB：873 条评论的 fetched_at **全部**落在 #30 的 UTC 08:01-08:15 窗口，#29 窗口（UTC 05:05-05:20 北京 13:05-13:20）**零数据**。
即 #29 静默失败——UI success ≠ 数据成功。详见 `.workbuddy/memory/2026-08-27.md`。

**已上线防御（2026-08-27）**：

- 新增 `scripts/ops/verify_release_upload.py`：daily collect 完成后立即用 `gh release view` 检查 `voc.db` asset（存在 + state=uploaded + size > 1KB）；失败 exit 1 让 GH Actions 步骤标红 → 邮件告警
- 配套 8 例 pytest：`tests/test_verify_release_upload.py` 覆盖 happy + 4 种失败（资产缺失/大小过小/state 非 uploaded/release 不存在）+ gh 缺失 + 兼容网页端上传的后缀
- 集成到 `.github/workflows/daily-collect.yml` 的「校验今日 Release asset」步骤（`if: always()`，在 collect 失败时也跑）

**blocker 现状**：手工 `voc-daily-bootstrap` release 已建（2026-08-23），bootstrap 累积有效。剩下风险：每次 scheduled run 仍可能因 GH 平台偶发问题静默失败 —— 新 defense 后**至少会派工单**。

### 8.4 决策待确认 Q1-Q5（plan §9，2026-08-19 已全部确认）

| # | 决策 | 当时建议 | 实际选择 |
|---|---|---|---|
| Q1 | DB 累积方式是否同意采用 GH Release asset | ✅ 推荐 | ✅ 已采纳 |
| Q2 | 监控目标是否先用现有 10 款 Steam + 1 条 B 站起步 | ✅ 推荐 | ✅ 实际：6 款 Steam（去掉 CS2/730 与补采难度较高的）+ 0 B 站（B 站为稳态快照不每日采） |
| Q3 | workflow 是否保留 artifact 上传作为 fallback | ✅ 推荐 | ✅ 已采纳 |
| Q4 | 首次跑前是否同意手动建 `voc-daily-bootstrap` release | ✅ 推荐 | ❌ **至今未执行**（见 §8.3 blocker） |
| Q5 | 回采 likes（7 天）是否合并入 daily workflow | ❌ 建议独立 | ✅ 已独立（`scripts/ops/refresh_likes.py` + 未来独立 workflow） |

### 8.5 每日时间窗策略 v2（2026-08-31 升级）

> **动机**：v1（`posted_after = max(posted_at) - 1 天`，无 `posted_before`）有两个实际痛点：
> 1. workflow 在 UTC 0:00（北京时间 8:00）跑，下载的是**昨天的 release**，而昨天 release 里的 max 通常是**前天 23:xx UTC**，导致 `posted_after = 前天 23:xx - 1 天 = 3 天前` —— **采集窗口永远落后 1-2 天**。
> 2. 没有 `posted_before`，每天有约 10.9% 的「当天」数据被混入（AGENTS.md 2026-08-27 audit 记录），违反「每天采昨天」的语义直觉。

**v2 设计**（`smart_window()` 函数，30 行）：

- **以北京日历日为准**（用户视角 = 北京时间；不依赖 UTC 时区切换）：
  - `posted_before = 北京当天 0:00 UTC 表示` = UTC T-1 16:00，由 `_passes_time_filter` 卡死当天边界
  - `posted_after = max(target_posted_after, 北京前天 0:00 UTC 表示)` = UTC T-3 16:00（floor 补救下限）
- **每款游戏独立窗口**（复用 `calc_posted_after`）：
  - `target_posted_after = max(posted_at) - 1 day`（v1 同款语义，保留去重优势）
  - 正常情况（昨天 workflow 成功）：`posted_after ≈ max_ts - 1d ≈ 北京前天 8:00`，floor 不生效
  - 补救情况（昨天 workflow 失败）：`posted_after = floor = 北京前天 0:00`，覆盖前天全天
- **整批共享 `now_utc`**：`main()` 算一次 `now_utc = _utcnow()` 传给每个 target，保证窗口基准一致

**行为矩阵**：

| DB max_ts（昨天 release） | posted_after            | 采集范围（北京日历日） |
|---|---|---|
| ≈ 北京昨天 8:00（正常）   | 北京前天 8:00           | 前天后半天 + 昨天全天 |
| ≈ 北京前天 8:00（昨天失败）| 北京前天 0:00（floor 生效）| 前天全天 + 昨天全天 ✓ |
| ≈ 北京前天 8:00（前天也败）| 北京前天 0:00（floor 生效）| 前天全天 + 昨天全天 |
| ≈ 北京大前天 8:00（连败 3d）| 北京前天 0:00（floor 生效）| 前天全天 + 昨天全天 |

**已知边界风险**：
- 8 小时窗口（[北京前天 0:00, 北京前天 8:00)）由「前天 workflow」覆盖，若前天失败由 floor 兜底
- 每天重复采「北京前天 0:00 ~ 8:00」（前天 workflow 已采过的 8 小时），靠 upsert 去重

**回归测试**（`tests/test_daily_incremental_collect.py`，新增 4 用例 = 10/10 全绿）：
1. `test_smart_window_normal_yesterday_max` — 正常场景，max 推进到昨天 8:00
2. `test_smart_window_recovery_floor_engages` — 补救场景，floor 生效
3. `test_smart_window_empty_db` — 空 DB 起步
4. `test_smart_window_bjt_midnight_boundary` — UTC 跨日（北京刚跨入新一天）

**为什么不上「每天重复采」开销**：
- 每款游戏每天多采 8 小时（前天 0:00 ~ 8:00），共 6 款 ≈ 6 页 Steam API 调用（~10 秒）
- upsert 去重保证数据正确性，重复评论被覆盖但不影响分析
- 用户明确「可以尝试补全前天的数据」（需求来自 `2026-08-29` 工程师对话）

**回滚路径**：把 `smart_window()` 替换回 v1 调用（`posted_after = calc_posted_after(...)`，`posted_before = None`），3 处签名改回 + 删 4 个 smart_window 测试用例。预计 5 分钟。

---

## 📋 版本记录

| 更新时间 | 内容 | 原因 |
|---|---|---|
| 2026-08-19 | 初版：P6 自动化流水线落地架构文档 | P6 自动化收口，解锁 P9 阶段 0 + P8 时间序列 |
| 2026-08-22 | §8 决策与风险历史新增；合并 `plan/P6_AUTOMATION_PIPELINE.md`；§7 故障排查加「release 存在但 assets 空」；更新「最后更新」日期 | 洁癖收口：消除两份 P6 文档重复；记录 A1 已知问题（每日 release asset 为空，累积 DB 未生效） |
| 2026-08-27 | §8.3 A1 升级：补真实案例（8/27 #29 silent 失败 UI success 但 0 数据）+ 上线防御（`scripts/ops/verify_release_upload.py` + GH Actions step + 8 例 pytest） | P6 silent 失败实战解锁工程师告警链路 |
| 2026-08-31 | **每日时间窗策略 v2**：§2.4 升级（以北京日历日为准 + 当天严格不采）；§8.5 新增设计记录；§3/§4 用例数 6→10；D4 决策标注 v1 弃用；新增 4 个 pytest；`scripts/ops/daily_incremental_collect.py` 加 `smart_window()` 函数（30 行） | 解决 v1 两个痛点：①窗口永远落后 1-2 天（昨天 release 的 max 通常是前天 23:xx UTC）；②每天混入 10.9% 当天数据 |