# B 站自动化采集设计（Bili Automation · 2026-08-23）

> **状态**：✅ 阶段 0 设计 + 落地（2026-08-23）｜ **调度**：本机计划任务 `VOC-Local-Daily-Collect`（北京 02:00，**单日上限 5 个视频**，见 §1.3.1）｜ **执行方**：`python -m src.queue ...` 或 Web 看板「系统管理」（2026-09-02 起可网页增删改）
> **关联**：[BILIBILI_COLLECTION.md](./BILIBILI_COLLECTION.md)（采集规格） · [AUTOMATION_PIPELINE.md](./AUTOMATION_PIPELINE.md)（P6 通用架构） · [WEB_DASHBOARD.md](./WEB_DASHBOARD.md)（系统管理页 §4.3）

---

## 一、需求与设计

工程师手动加 BV 号到「待采清单」，系统识别投稿时间后自动计算第 7 天 = 采集日，**每天由本机计划任务（北京 02:00）扫今天到期的视频触发采集，单次最多 5 个**（见 §1.3.1）。已采过的标 fetched 并记时间。Web 看板「系统管理」可网页增删改 + 暂停/恢复（2026-09-02）。

### 1.1 状态机

```
              add (识别 pubdate 成功)
   pending ─────────────────────→ scheduled
                                    │
                                    │ cron 扫到 due_date <= today
                                    ▼
                                 fetching
                                ╱       ╲
                          成功 ╱         ╲ 失败
                              ▼           ▼
                          fetched      (fail_count += 1)
                              │           │
                              │           │ fail_count < 3 → 回 scheduled
                              │           │ fail_count >= 3 → failed (dead-letter)
                              ▼           ▼
                          (永久)       failed (永久)

   ── 人工暂停（Web 看板，2026-09-02 新增）──
   任意非终态（pending/scheduled/fetching/failed）──pause──→ paused
   runner 只扫 status='scheduled'，paused 天然被跳过
   paused ──resume──→ scheduled（pubdate 已识别）/ pending（未识别）
   （resume 仅 paused 可恢复；fetched 不可暂停也不可恢复——重采走显式 revisit）
```

### 1.2 表结构（`bilibili_queue`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `bv_id` | VARCHAR(20) PK UNIQUE | B 站视频 BV 号 |
| `title` | VARCHAR(512) | 视频标题（识别后填入） |
| `pubdate` | DATETIME | 投稿时间（识别后填入） |
| `due_date` | DATETIME | = pubdate + 7d，cron 用此判断 |
| `status` | VARCHAR(16) | pending/scheduled/fetching/fetched/failed/**paused**（2026-09-02 新增） |
| `added_at` | DATETIME | 加入时间 |
| `added_by` | VARCHAR(32) | manual（未来扩展：keyword:xxx / up:xxx） |
| `fetched_at` | DATETIME | 采集完成时间 |
| `comment_count` | INT | 采到的评论数 |
| `danmaku_count` | INT | 采到的弹幕数 |
| `fail_count` | INT | 失败计数（默认 0） |
| `fail_reason` | TEXT | 最后失败原因 |
| `revisit` | BOOL | high-value 重采标记 |
| `note` | TEXT | 工程师备注 |

索引：`ix_biliq_status_due (status, due_date)` —— 每日扫描查询的核心路径

### 1.3 调度行为（现役：本机计划任务）

**驱动方**：本机 Task Scheduler `VOC-Local-Daily-Collect`（北京 02:00，注册脚本
`scripts/ops/register_local_collect_task.ps1`）。B站采集是**该任务链上的一个阶段**，不是独立任务 ——
由 `daily_incremental_collect.py` 的 `run_bilibili_queue()` 调用（2026-09-05 接入；
`--skip-bilibili` 关闭、`--bili-limit` 调参）。

#### 1.3.1 每日吞吐上限：5 个视频/天 ⭐

| 项 | 值 / 行为 | 依据 |
|---|---|---|
| **单次上限** | **5** | 参数 `--bili-limit`（代码默认 `run_bilibili_queue(limit=5)`；计划任务**未覆盖**该参数 → 实际走默认 5） |
| **为什么是 5** | 防风控 + 时长预算 | 单视频 1–3 分钟 → 最坏 ~15 分钟，为计划任务的 150 分钟上限留足余量；B站风控对短时高频请求敏感 |
| **查询与排序** | `status='scheduled' AND due_date <= today` **ORDER BY `due_date` ASC LIMIT 5** | 先到期的先采 |
| **超额怎么办** | **只顺延、不丢弃** | 今天没轮到的**仍是 `scheduled`**（不改状态），次日或下一次运行继续 —— 不会跳过、不会漏采 |
| **连续大批量录入** | 线性顺延 | 一次录 20 条 ≈ 4 天消化完；要当天清走下面的加速手段 |
| **加速手段** | ① 手动 `python -m src.queue run-due --limit N`（临时）；② 调大计划任务的 `--bili-limit`（持久，注意风控与 150 分钟预算） | — |
| **观测方法** | `python -m src.queue due`（今天到期，含被顺延的）；`python -m src.queue list --status scheduled`（看积压总量） | — |

> ⚠️ **队列持续积压时先分清原因**：是单日上限卡住（`due` 输出 > 5 条），还是单条反复失败进了
> `failed`（看 `fail_count` / `fail_reason`）—— 两者的处置完全不同（前者调上限，后者查风控/接口）。

> 📌 **历史沿革（2026-09-11）**：云端 workflow 原本用 `run-due --limit 50`，随
> `.github/workflows/bilibili-daily.yml` 一并删除（实测它在 2026-09-03 ~ 09-10 **每天真跑一次**）。
> 删除后单日上限由 50 降为 5 —— 这是**为换取「不重复采集、不白烧标注 token」而有意接受的
> 吞吐回退**，不是 bug。

#### 1.3.2 单条任务的执行逻辑

1. 查 `status='scheduled' AND due_date <= today` ORDER BY `due_date` LIMIT 5（见 §1.3.1）
2. 逐个标 `fetching` → 调 `src.pipeline.run_pipeline(platform='bilibili', target_id=bv_id)`
3. 成功 → 标 `fetched`，记 comment/danmaku 数
4. 失败 → `fail_count += 1`；< 3 次回 `scheduled` 重试；≥ 3 次 dead-letter（`failed`，永久）

### 1.4 与 Steam daily 的对比

| 维度 | Steam daily | B 站 daily |
|---|---|---|
| 触发 | 本地计划任务 `VOC-Local-Daily-Collect`（北京 02:00） | **同一条链的后置阶段**（`run_bilibili_queue`，2026-09-05 接入） |
| 采集目标来源 | `collect_tasks` 表（空表回落 `config/monitoring/targets.yaml`） | `bilibili_queue` 表（动态；Web 看板可增删改） |
| 数量 | 6 款 Steam 单机（固定） | **≤ 5 视频/天**（`--bili-limit`，见 §1.3.1） |
| 新增目标方式 | Web 看板「系统管理」或改 yaml | `python -m src.queue add BV...` 或 Web 看板 |
| 失败重试 | 单 target try/except，仅记 warning | `fail_count` 计数，≥ 3 次 dead-letter |
| 数据落点 | 同一个 `data/voc.db`，链末尾 `--push-db` 推 VPS | 同左 |

---

## 二、CLI（python -m src.queue <subcmd>）

### 2.1 add —— 录入待采清单

```bash
# 单个
python -m src.queue add BV1UpwaeNESx

# 多个
python -m src.queue add BV1UpwaeNESx BV2xxxxxxxx BV3xxxxxxxx

# 接受完整 URL
python -m src.queue add https://www.bilibili.com/video/BV1xxxxxxxx
```

行为：
1. 调 B 站 view 接口识别 pubdate + title
2. 若识别成功 → status=scheduled，due_date = pubdate + 7d
3. 若识别失败（-412 / 网络）→ status=pending，pubdate=None（下次 cron 或人工补识别）

### 2.2 list —— 列出条目

```bash
# 全部
python -m src.queue list

# 按 status 过滤
python -m src.queue list --status pending
python -m src.queue list --status scheduled
python -m src.queue list --status failed
```

输出：表格 + 状态统计。

### 2.3 due —— 列今天到期的任务

```bash
python -m src.queue due
python -m src.queue due --limit 10
```

输出今天 due_date 的视频（status=scheduled AND due_date <= today）。

### 2.4 run-due —— 立即触发今天的采集

```bash
# 实际跑
python -m src.queue run-due

# 只看不跑
python -m src.queue run-due --dry-run

# 限 5 个（本地调试）
python -m src.queue run-due --limit 5
```

由 `src.queue.runner.run_due_collection` 实现，被 CLI 和 workflow 共用。

### 2.5 skip —— 跳过某个 BV

```bash
python -m src.queue skip BV1xxxxxxxx --reason "非评测视频"
```

直接标 failed（不重试），用于人工判断后主动跳过。

### 2.6 remove —— 删除条目（仅 pending/scheduled/failed）

```bash
python -m src.queue remove BV1xxxxxxxx
```

注意：fetched 的不允许删除（避免误删历史采集）。

### 2.7 show —— 显示详情（JSON）

```bash
python -m src.queue show BV1xxxxxxxx
```

输出完整 row 字典（含 title / pubdate / due_date / status / fetched_at / comment_count 等）。

---

## 三、failure 处理策略

### 3.1 失败重试

```python
MAX_FAIL_COUNT = 3   # 单视频失败 3 次后入 dead-letter
```

- 失败 1-2 次：仍 scheduled，下次 cron 重试
- 失败 3 次：dead-letter（status=failed）+ `$GITHUB_STEP_SUMMARY` 报告

### 3.2 常见失败原因

| 失败 | 处理 |
|---|---|
| B 站 API 返回 -412（风控） | 记 fail，下次 cron 重试；建议当天停手 |
| B 站 API 返回 -352（登录失效） | 记 fail + 提示「检查 BILIBILI_SESSDATA」 |
| `comments.target_id UNIQUE` 冲突（已采过）| 标 fetched，结束 |
| 网络超时 | 记 fail，下次重试 |

### 3.3 单批防风控

- 单次任务 ≤ 50 视频（BILIBILI_COLLECTION.md §四4.5）
- 视频间隔 1-3s 随机（已在 collector 内实现）
- 整批失败告警（写 `$GITHUB_STEP_SUMMARY`）

---

## 四、当前已落地（2026-08-23）

| 组件 | 状态 |
|---|---|
| `bilibili_queue` 表 + ORM 模型 | ✅ |
| CLI（add / list / due / run-due / skip / remove / show） | ✅ |
| Runner（`src.queue.runner.run_due_collection`） | ✅ |
| 调度（2026-08-23 云端 workflow → **2026-09-11 起本地计划任务**，见 §1.3） | ✅ |
| 单元测试（`tests/test_bilibili_queue.py` 6 例） | ✅ |
| 设计文档（本文件） | ✅ |

---

## 五、未来扩展（v2）

### 5.1 关键词 / UP 主自动入候选池

新增 `bilibili_candidate` 表或加 `status='candidate'` 字段。workflow 增加 job：
- 扫 `config/monitoring/bilibili_keywords.yaml`
- 调 `x/web-interface/search` 找过去 7 天发布的
- 入 candidate 池（不立即采）
- 工程师 / 周会 review（CLI: `bili-queue promote BVxxx`）
- promote 后才进 due 队列

### 5.2 前端可视化（「系统管理」界面）

需求来自工程师：「未来，我计划把这个「待采清单」可视化做到前端的「系统管理」类界面。」

实现方式：
- Streamlit 侧边栏加「系统管理」页（与「单目标看板 / 多目标对比」并列）
- 读 `bilibili_queue` 表，显示：
  - 待采清单（pending + scheduled）+ 加 BV 输入框
  - 今天到期（due） + 立即运行按钮
  - 历史采集（fetched）+ 时间线
  - dead-letter（failed）+ 重试 / 跳过 / 删除
- API 层：复用 `src.queue.cli` 的函数（改为 FastAPI 端点）

设计原则：纯读 `bilibili_queue` 表 + 调 `add` / `skip` / `run-due`，不引入新数据流。

### 5.3 high-value 重采

`revisit` 字段已预留。下次扫描时：
- 若 `revisit=true` 且距上次 `fetched_at` > 30 天 → 自动重入 due 队列

由工程师人工标 revisit（CLI：`python -m src.queue mark-revisit BVxxx`）。

---

## 七、成本基线

| 项 | 单视频量级 |
|---|---|
| BV 录入 + pubdate 识别 | 1 次 API 调用 ≈ ¥0 |
| 单视频采集 | 1006 评论 + 1200 弹幕 ≈ 3 分钟（免费） |
| LLM 打标 | 1,000 条 ≈ ¥5 |
| 总（每周 5 个新视频） | ≈ ¥25/周 |

---

## 八、相关文档

- [BILIBILI_COLLECTION.md](./BILIBILI_COLLECTION.md)：采集规格（T=1000 / K=1000 / 阈值分支等）
- [AUTOMATION_PIPELINE.md](./AUTOMATION_PIPELINE.md)：P6 通用架构（GH Release 累积 DB）
- [DEVELOPMENT_PLAN.md §四 P6](../plan/DEVELOPMENT_PLAN.md)：与 Steam daily 的关系