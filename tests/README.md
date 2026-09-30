# 🧪 测试（tests/）

> **pytest 自动回归门禁** — 不放脚本，CI 与本地共用同一套用例。
>
> **最后更新**：2026-09-30（LLM 批量输出健壮性：截断/JSON 残缺不再放大成整批异常 + `raise_on_error` 口径收敛为「系统性故障才抛」，新增 `test_llm_batch_robustness.py` 18 例；用例数 271 → **289**，并用 `pytest --collect-only` 全量复核本节各文件用例数）

---

## 🗺️ 测试地图

```
tests/
├── README.md                       ⬅ 你在这里：tests/ 用例索引
├── __init__.py                     包标记（空文件）
├── test_pipeline.py                 🔬 主流程 pipeline 端到端
├── test_analysis_pipeline.py        🏷️  主链路分析→落盘（观点/topic/失败不固化）
├── test_golden_match.py             🏆 黄金集回归门禁（GDT v3.1.1 L3）
├── test_embedding.py                🧠 本地 bge 向量化（无 ML 环境时 skip）
├── test_analyzer_version.py         📌 P10 analyzer_version 溯源
├── test_bilibili_queue.py           📋 B 站采集队列（状态机 / 约束）
├── test_check_daily_collect.py      🛡️  每日采集哨兵判定（2026-09-07）
├── test_monitored_targets.py        🎯 monitored 白名单并集 + 零数据任务可见（2026-09-06）
├── test_daily_incremental_collect.py ⏰ P6 每日采集（smart_window v2）
├── test_verify_release_upload.py    🛡️  P6 silent 失败防御
├── test_collect_tasks.py            🗃️  collect_tasks 表 + 种子迁移 + WAL + B站 paused（2026-09-02）
├── test_api.py                      🌐  Web API 端点 + 鉴权 + 任务 CRUD（2026-09-02）
├── test_steam_collector.py          🚰  Steam 空响应重试 + 应用层时间窗（2026-09-03）
├── test_snapshot_export.py          📤  静态快照导出（2026-09-07）
├── test_wordcloud.py                ☁️  评论词云（jieba + TF-IDF + 情感着色，2026-09-08）
├── test_bulk_upsert_preserves_analysis.py 🛡️  重采保全已分析数据（2026-09-08）
├── test_p1_hardening.py             🔒  P1 内测加固（审计/公网自检/脱敏/展示端只读，2026-09-11）
├── test_llm_batch_robustness.py     🧯  LLM 批量输出健壮性（截断/JSON 残缺 → 降批重试，2026-09-30）
├── test_agent_schema.py             🤖  Agent 表结构 + FK CASCADE（2026-09-09）
├── test_agent_endpoints.py          🤖  Agent API 端点回归（sessions/chat/export/search/rate-limit）
├── test_agent_chat_loop.py          🤖  Agent tool_call 循环 + SSE + skill 注入
├── test_agent_skills.py             🤖  Skill YAML 匹配 + tool schema yaml 加载
├── test_agent_tools.py              🤖  4 个 tool 实现（query_overview/topics/comments/search_docs）
├── test_prune_agent_history.py      🧹  30 天 agent 会话裁剪（2026-09-09）
└── fixtures/                        📦 测试夹具（不入 scripts/）
    ├── golden_match_set.json         410 条 L3 匹配真值（黄金集）
    └── golden_overrides.json         人工校正项（覆盖部分黄金集）
```

---

## 📊 当前用例统计

- **共 289 例**（pytest 2026-09-30 实测 **288 passed / 1 skipped**；上版 49 → 78 → 82 → 94 → 97 → 110 → 129 → 138 → 204 → 210 → 214 → 219 → 250 → 257 → 259 → 271 → 284 → **289**）
- 1 例 ML 环境依赖跳过（`test_embedding.py`，无 torch 时 skip；本机 .venv-ml 有 torch 时全量跑）
- CI 跑通门禁：`pytest tests/` 在 push / cron 都跑（workflow `test:` job）
- `requirements-core.txt` 已含 fastapi/uvicorn/httpx/itsdangerous（`test_api.py` 依赖）

---

## 各测试文件

### `test_pipeline.py` · 主流程端到端

| 项 | 值 |
|---|---|
| **覆盖** | `python -m src.pipeline` 全流程：采集 → 入库 → 分析 → 向量化 → 回写 |
| **用例数** | 8 |
| **更新** | 2026-08-16 |
| **依赖** | 独立测试 DB（不污染 `data/voc.db`） |

### `test_analysis_pipeline.py` · 主链路分析→落盘端到端

| 项 | 值 |
|---|---|
| **覆盖** | `run_pipeline` 分析阶段：观点写入 `comment_opinions`、topic 由核心观点映射、**批级失败向上抛不固化 `analyzed_at`**、**失败占位（conf=0 且无观点）跳过落库**、**越界 topic 被 `valid_l1_labels` 过滤** |
| **用例数** | 4 |
| **更新** | 2026-09-21（对抗审查 P1#1 / P3#2 回归） |

### `test_golden_match.py` · **黄金集回归门禁** ⭐

| 项 | 值 |
|---|---|
| **覆盖** | 用 `fixtures/golden_match_set.json` 410 条人工标注的真值，验证 `match_l3()` 全集匹配准确率 |
| **用例数** | 1（黄金集整体准确率单测） |
| **更新** | 2026-08-18 |
| **触发场景** | **词典 / 标签 / match 规则改动后必跑**；GDT v3.1.1 阶段 1 收口时通过率 100% |

### `test_embedding.py` · 本地向量化

| 项 | 值 |
|---|---|
| **覆盖** | `src/analyzers/embedder.py` bge-small-zh-v1.5 加载 + 向量化 + `semantic_search` |
| **用例数** | 1（**无 ML 环境时自动 skip**） |
| **更新** | 2026-08-11 |
| **注** | CI 端（`.github/workflows/ci.yml` 的 `test:` job）不装 torch / sentence-transformers；本地有 ML 环境时跑全量 |

### `test_analyzer_version.py` · P10 analyzer_version 溯源 ⭐

| 项 | 值 |
|---|---|
| **覆盖** | `comments.analyzer_version` 字段：写入 / 不擦旧值 / prompt hash 稳定 / prompt 变动联动 / LLM format `llm:{model}@{prompt_hash8}` / local format / pipeline CLI choices 含 `glm-5.3-flash` / 缺属性兼容 / init_db 自动 ALTER |
| **用例数** | 14 |
| **更新** | 2026-08-31（GLM-5.3-Flash provider + GLM_API_KEY 命名同步） |
| **门禁** | 换模型 / 换 prompt 后**必跑**，确保存量数据可按 version 分组重打或比对 |

### `test_bilibili_queue.py` · B 站采集队列

| 项 | 值 |
|---|---|
| **覆盖** | `src/queue/` 状态机：`bilibili_queue` 表 / 状态转换 / 查询 / 唯一约束 / 重访标记 / 序列化 |
| **用例数** | 17 |
| **更新** | 2026-09-21（+`Danmaku.to_dict` 归位回归；2026-09-05 内置 B站 run-due 编排回归 + 孤儿 fetching 回收 + pending 重识别 + 零评论失败判定） |

### `test_check_daily_collect.py` · 每日采集哨兵

| 项 | 值 |
|---|---|
| **覆盖** | `scripts/ops/check_daily_collect.py` 判定逻辑：`should_backfill` 四分支（失败/成功/运行中/未跑）+ 孤儿进程防护 |
| **用例数** | 6 |
| **更新** | 2026-09-07 |
| **门禁** | 哨兵判定逻辑改动后必跑 |

### `test_monitored_targets.py` · monitored 白名单并集

| 项 | 值 |
|---|---|
| **覆盖** | `service.list_targets_payload(monitored=true)`：yaml 白名单 ∪ collect_tasks 并集；collect_tasks 零数据任务可见；归档网游不可见 |
| **用例数** | 2 |
| **更新** | 2026-09-06 |
| **门禁** | 前端目标下拉 / monitored 语义改动后必跑 |

### `test_daily_incremental_collect.py` · P6 每日采集

| 项 | 值 |
|---|---|
| **覆盖** | `smart_window()` v2 行为矩阵：normal（正常采集）/ recovery（补救，覆盖前天全天）/ empty（空 DB 起步）/ BJT 跨 UTC 日界 |
| **用例数** | 20 |
| **更新** | 2026-09-12（+哨兵补采 `--push-db` flag 回归） |
| **关联** | `scripts/ops/daily_incremental_collect.py`（**本机 Task Scheduler 02:00 入口**；GH Actions 采集 workflow 已于 2026-09-11 删除） |

### `test_collect_tasks.py` · collect_tasks 表 + 种子迁移 + WAL + paused ⭐（2026-09-02）

| 项 | 值 |
|---|---|
| **覆盖** | `src/storage/db.py`：`CollectTaskRepository` CRUD（创建/重复拒绝/暂停恢复/编辑/删除）/ `seed_collect_tasks_from_yaml` 幂等与 excluded 过滤 / `load_targets_from_db` + `load_targets_any` 回退链 / SQLite WAL 模式 / `bilibili_queue` paused 被 runner 跳过 |
| **用例数** | 15 |
| **更新** | 2026-09-02（WEB_DASHBOARD.md 阶段 2 存储层验收） |
| **门禁** | Web 看板「系统管理」改采集任务后，`daily_incremental_collect.py` 仍能正确加载目标 |

### `test_api.py` · Web API 端点 + 鉴权 + 任务 CRUD ⭐（2026-09-02）

| 项 | 值 |
|---|---|
| **覆盖** | `src/api/` 全部端点：health/targets/overview/topics/comments/trends（含 analyzed + fallback_pct）/compare + 管理员登录（正确/错误密码）+ 未登录 401 + Steam 任务新增(URL 解析/重复 409/暂停恢复/删除) + B 站任务（识别 pubdate/due 计算/pause/resume/reidentify/fetched 禁删 409/无效 BV 422） |
| **用例数** | 50 |
| **更新** | 2026-09-21（+对抗审查回归：games/meta 目标截断 8 / 非法 target 不落库 / 封面 appid 白名单 / `include_hidden` 公开通道失效 / `q` LIKE 转义；2026-09-07 admin 排序/发行时间/采集时间 + 多选上限等回归） |
| **外部依赖** | fastapi + httpx（`requirements-core.txt` 已加）；Steam appdetails / B 站 view / backfill 线程全部 mock，不出网 |

### `test_steam_collector.py` · Steam 采集器（空响应重试 + 时间窗）⭐（2026-09-03）

| 项 | 值 |
|---|---|
| **覆盖** | `src/collectors/steam.py`：①Steam 瞬时空响应 → 同 cursor 退避重试 ×2 后恢复采集（修复前首页即空直接 break = 静默丢一整天数据）；②应用层时间窗（posted_before 之后排除）；③连续 3 次空响应终止（防死循环）；④`fetch_app_info` 2 次重试与重试耗尽 |
| **用例数** | 5（fake session，不出网） |
| **更新** | 2026-09-06（底特律 9/3 02:00 fetched=0 事故 + 9/5 admin「查找」随机失败修复） |
| **门禁** | 任何采集器分页/终止逻辑改动后必跑 |

### `test_verify_release_upload.py` · P6 silent 失败防御 ⭐

| 项 | 值 |
|---|---|
| **覆盖** | `scripts/ops/verify_release_upload.py` GH Release asset 校验逻辑（size > 1KB + state=uploaded + 缺失/异常检测） |
| **用例数** | 8 |
| **更新** | 2026-08-27（与 workflow `daily-collect.yml` 新增「校验今日 Release asset」步骤同周上线） |
| **门禁** | P6 silent 失败（assets=[] 但 workflow 仍 success）的根治防御 |

### `test_wordcloud.py` · 评论词云（compare 页）⭐

| 项 | 值 |
|---|---|
| **覆盖** | jieba 分词 + 停用词/噪声过滤、跨游戏 TF-IDF 区分度、词内多数情感着色、窗口过滤与缓存失效（**含正文被改写后失效**）、词频门槛 |
| **用例数** | 5 |
| **更新** | 2026-09-21（+P3#3 内容指纹回归） |

### `test_p1_hardening.py` · P1 内测加固

| 项 | 值 |
|---|---|
| **覆盖** | 访问审计中间件（缓冲/落库/失败吞掉/XFF 末段）、`PUBLIC_MODE=1` 启动自检（fail-closed）、B站评论对外脱敏、展示端只读（`display_only` 拒绝 pipeline）、root 日志配置、**登录失败桶容量上限** |
| **用例数** | 21 |
| **更新** | 2026-09-21（+P2#5 登录失败桶容量回归） |

### `test_llm_batch_robustness.py` · LLM 批量输出健壮性 ⭐

| 项 | 值 |
|---|---|
| **背景** | 2026-09-30 实测定位：DeepSeek 在含 2-3 条百字评论的批次上 `finish_reason=length`（completion 正好撞死 `MAX_OUTPUT_TOKENS=2500`），JSON 被截断；而 `_parse_batch` 贪婪正则兜底分支的**第二次 `json.loads` 无保护** → `JSONDecodeError` 穿透 `analyze_batch`，把「一次输出太长」放大成「整个 target 当天失败」 |
| **覆盖** | `extract_json_object` 永不抛异常（截断 → None）；截断/不可解析/**缺 index**/`results` 空 → **对半降批重试**（拆到 1 条）；健康批次仍一次请求打满；单条答不动 → 失败占位且**不经过 `_finalize`**（防被兜底匹配成「1 观点 + conf=0」的假成功）；**个别条坏不抛、整批全废才抛**（`ok_total == 0`）；API/网络异常**不降批**；`MAX_OUTPUT_TOKENS ≥ 8000` 且透传 |
| **用例数** | 18 |
| **更新** | 2026-09-30 |
| **依赖** | **不联网**：`client` 换成可编程 Fake；需 `openai` 包（`requirements-core.txt` 已含） |

### `test_snapshot_export.py` · 方案③ 静态快照导出

| 项 | 值 |
|---|---|
| **覆盖** | `scripts/ops/export_static_snapshot.py`：路由表精确匹配（`*` 通配非空）、清单结构、零数据目标跳过、导出前后一致性 |
| **用例数** | 9 |
| **更新** | 2026-09-07 |

### `test_bulk_upsert_preserves_analysis.py` · 重采不覆盖已分析结果

| 项 | 值 |
|---|---|
| **覆盖** | `bulk_upsert` 重采同一 `source_id` 时**不得覆盖**既有情感/topic/`analyzed_at`（2026-09-08 对抗审查 P3 回归） |
| **用例数** | 2 |
| **更新** | 2026-09-08 |

### `tests/test_agent_*.py` · 原声分析 Agent（2026-09-09）

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `test_agent_chat_loop.py` | 10 | chat tool_call 循环（多轮 tool → 收尾；越权 fail-closed） |
| `test_agent_endpoints.py` | 27 | `/api/agent/*` 端点：sessions CRUD / 分页 total / 导出隔离 / 归属校验 |
| `test_agent_schema.py` | 8 | `agent_sessions` / `agent_messages` 表结构与序列化 |
| `test_agent_skills.py` | 13 | skill 匹配（`required_tools` 交集 → prompt 注入） |
| `test_agent_tools.py` | 19 | 4 个 tool 真实实现（overview/topics/comments/search_docs） |
| `test_prune_agent_history.py` | 6 | 30 天滚动裁剪（dry-run / 级联删 / 保留策略） |

---

## `fixtures/` · 测试夹具

| 文件 | 大小 | 作用 | 更新 |
|---|---|---|---|
| `golden_match_set.json` | 61 KB | **410 条 L3 匹配真值**（人工标注，含 override 标记），`test_golden_match.py` 回归门禁数据 | 2026-08-17 |
| `golden_overrides.json` | 5 KB | **人工校正项**（覆盖黄金集部分条目的预期匹配结果） | 2026-08-18 |

> ⚠️ fixtures/ 内是**人工标注真值 + 业务知识**，不是临时数据；按 AGENTS.md §1 放 `tests/fixtures/` 不放 `scripts/`。

---

## 🔧 怎么跑

```bash
# 全量
pytest tests/

# 单独跑某个
pytest tests/test_golden_match.py -v

# 跳过 ML 测试（无 torch 环境）
pytest tests/ --deselect tests/test_embedding.py

# 完整门禁（推荐 commit 前）
pytest tests/ && python scripts/smoke_test.py
```

CI 在 `.github/workflows/ci.yml` 的 `test:` job 自动跑（**push / pull_request 双触发** + 手动 dispatch —— 2026-09-11 起；此前是 `daily-collect.yml` 的 cron 触发，那两个采集 workflow 已删除）。

---

## ⚠️ 测试约束（AGENTS.md §3 工程红线）

- **独立测试 DB**：所有用例都用临时 DB（`tmp_path` 或 `voc_test_*.db`），**禁止污染 `data/voc.db` 主库**
- **不联网**：除 mock 外，pytest 用例不依赖网络
- **不写死时间戳**：用 `datetime.now()` / fixture 注入，不用 `2026-08-31` 这种硬编码
