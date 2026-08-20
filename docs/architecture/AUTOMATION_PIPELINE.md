# 自动化采集流水线（P6 · 落地架构）

> **用途**：让任何工程师/产品/评审同事能 30 秒内理解"每天 00:00 UTC 自动化采集是怎么跑的、数据怎么累积"。
>
> **关联文档**：
> - 路线图：[plan/P6_AUTOMATION_PIPELINE.md](../plan/P6_AUTOMATION_PIPELINE.md)（设计决策 + 验收标准）
> - 路线图总纲：[plan/DEVELOPMENT_PLAN.md](../plan/DEVELOPMENT_PLAN.md)
> - 主采集器：[pipeline.py](../../src/pipeline.py) + [steam.py](../../src/collectors/steam.py)
> - 存储层：[DATA_STORAGE_DESIGN.md](./DATA_STORAGE_DESIGN.md)
>
> **最后更新**：2026-08-19
> **状态**：✅ 已落地（2026-08-19）

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

### 2.4 增量语义 = 滑窗 + DB 去重

- **采集时间窗**：`posted_after = max(posted_at) - 1 天`（1 天缓冲，覆盖边界漏采）。
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
| `tests/test_daily_incremental_collect.py` | 6 个回归用例（空库起步/时间窗/不擦旧数据/单失败容错/gh 容错） |
| `docs/plan/P6_AUTOMATION_PIPELINE.md` | 设计 + 实施步骤（计划阶段产物） |

---

## 4. 验收（已通过 2026-08-19）

- [x] `pytest tests/test_daily_incremental_collect.py` 全 6 用例绿
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

---

## 📋 版本记录

| 更新时间 | 内容 | 原因 |
|---|---|---|
| 2026-08-19 | 初版：P6 自动化流水线落地架构文档 | P6 自动化收口，解锁 P9 阶段 0 + P8 时间序列 |