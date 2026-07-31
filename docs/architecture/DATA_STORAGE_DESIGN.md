# VoC 平台 · 数据存储架构设计

> **用途**：把"原始 / 清洗 / 标注 / 仪表盘 / 导出" 5 层数据建模的约定一次性讲清楚。
> 适用所有采集器、所有 LLM 服务商、所有数据分析师。
>
> **关联文档**：
> - 字段三级分类（与本设计的"前缀约定"配套）：[DATA_FIELDS.md](./DATA_FIELDS.md)
> - 完整开发计划：[DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md)

---

## 🎯 核心结论（先给一句话）

> **5 个处理环节 → 对应 5 层数据，但只需要 4 张数据库表 + 1 套文件目录。仪表盘查询的是视图，不存物理表。**

避免走进"一张表塞下所有数据"的反模式。

---

## 📐 一、5 层数据架构总览

```
┌────────────────────────────────────────────────────────────┐
│  L1 原始层   raw_comments                       平台API原文  │ ← 不可变存档
├────────────────────────────────────────────────────────────┤
│  L2 清洗层   cleaned_comments                   字段归一化  │ ← 历史快照（可重放）
├────────────────────────────────────────────────────────────┤
│  L3 标注层   tagged_comments                    LLM 标签   │ ← 主业务表
├────────────────────────────────────────────────────────────┤
│  L4 视图层   (SQL View / dashboard query)                 │ ← 不存表，查询时拼装
├────────────────────────────────────────────────────────────┤
│  L5 导出层   data/exports/*.csv|json                      │ ← 文件形式
└────────────────────────────────────────────────────────────┘
```

**为什么 5 层而不是 1 张大表**：

| 痛点 | 单表方案的结果 | 分层方案的结果 |
|---|---|---|
| 改 prompt 想重跑分析 | 没原始数据可回退 | 直接从 L1 重跑 |
| 多平台字段差异 | 表会越来越胖、字段歧义 | L1 用 JSON 存各平台字段 |
| 数据出问题时追溯 | 不知道改过几次 | L2/L3 都在 |
| 想看一次跑多少 / 失败率 | 没记录 | pipeline_runs 日志 |
| 设计师要导出数据写用例 | 自己 JOIN SQL | 视图一键导出 |

---

## 🗂️ 二、4 张核心表（SQLite，同库多表）

> 个人项目不引入第二个数据库。SQLite 单库 + 多表，完全够撑到 10W 条月度。

### 表 1：`targets` （分析对象元数据）

> **存什么**："被分析的东西"是什么 —— 某个游戏 / 某个视频 / 某个品牌话题。
> **好处**：跨评论来源时，能直接按"游戏名"圈出全部评论，不必 JOIN 评论表的字符串。

| 字段 | 类型 | 业务含义 | 写入时机 |
|---|---|---|---|
| `target_id` | str (PK) | `"steam:730"`、`"bilibili:video:abc123"` | 自动生成 |
| `platform` | str | steam / bilibili / weibo | |
| `external_id` | str | Steam appid / B 站 BV 号 | |
| `name` | str | "Counter-Strike 2" | 拉详情后回填 |
| `type` | str | game / video / topic |  |
| `extra_json` | text | 平台特有的元信息（开发商、发行日期、UP 主等） | |
| `first_seen_at` | datetime | 我们第一次见到它的时间 | |
| `last_synced_at` | datetime | 最近一次刷新元数据的时间 | |

### 表 2：`raw_comments` （**L1 原始层**）

> **存什么**：采集器从 API 拉回来的"原文照搬"，一个字都不改。
> **好处**：原始存档，重跑清洗 / 重跑 LLM 都不用调上游接口（省钱 + 抗变更）。

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `raw_id` | int (PK) | 自增 |
| `target_id` | str (FK) | 关联到 `targets` |
| `platform` | str | steam / bilibili / weibo |
| `source_id` | str | 平台原生 ID（Steam `recommendationid`、B 站 `rpid`） |
| `author_id` | str \| null | 仅匿名 ID，不存昵称 |
| `content` | text | 评论原文 |
| `rating` | int \| null | 1/0/-1（部分平台无评分） |
| `language` | str | `schinese` / `english` |
| `likes` | int | 点赞数 |
| `replies` | int | 回复数 |
| `posted_at` | datetime | 评论发布时间 |
| `raw_json` | text | **全量 API 返回原文** ← 这个最关键 |
| `extra_json` | text | 各平台特有字段（Steam `playtime_forever`、B 站 `oid` 等） |
| `fetched_at` | datetime | 入库时间 |
| `fetched_by` | str | `pipeline@steam-2026-07-31T22-35` |

**唯一键约束**：`unique(target_id, platform, source_id)` —— 同一评论入库不重复

### 表 3：`cleaned_comments` （**L2 清洗层**）

> **存什么**：经过清洗（去 HTML 标签、统一语言、删空文本、合并重复）后的"稳定版本"。
> **好处**：改清洗规则不会污染原始数据，对 LLM 而言这是稳定的输入。

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `clean_id` | int (PK) | 自增 |
| `raw_id` | int (FK) | 反查原始记录 |
| `target_id` | str | 冗余存（便于 join） |
| `platform` | str | |
| `source_id` | str | |
| `cleaned_text` | text | 清洗后的文本（去标签、去表情符、合并多行等） |
| `language` | str | 标准化（统一 zh-CN / en-US） |
| `is_valid` | bool | 是否可用（空文本、纯表情符会被标 false） |
| `invalid_reason` | str \| null | 为什么无效 |
| `cleaned_at` | datetime | 清洗时间 |
| `cleaned_by_version` | str | 清洗规则版本号（`v1.0.0`） |

**清洗流程的版本控制** —— 这个字段是设计上的小亮点：
> 如果清洗规则变了（v1 → v2），重跑清洗时给旧记录也补上新版本号，可知道"哪些是用老规则洗的"。未来上 A/B 评测规则效果时很有用。

### 表 4：`tagged_comments` （**L3 标注层** · 主业务表）

> **存什么**：经过 LLM 分析后的最终态 —— 是仪表盘、看板、报告**直接查的表**。
> **好处**：把 LLM 推理做成"可重跑 + 可对比"，换了模型/换了 prompt 都能覆盖更新。

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `tagged_id` | int (PK) | 自增 |
| `clean_id` | int (FK) | 反查清洗记录 |
| `raw_id` | int (FK) | 也可以直接反查原始 |
| `target_id` | str | 冗余存 |
| `sentiment` | str | positive / negative / neutral |
| `sentiment_score` | float | -1 ~ +1 |
| `sentiment_confidence` | float | 0 ~ 1 |
| `topic` | str | 主主题（已在前缀约定：C_） |
| `sub_topics` | text (JSON) | ['画质', '手感'] |
| `model` | str | `deepseek-v3` / `qwen-turbo` / `glm-4-flash` |
| `prompt_version` | str | `v1.0.0`，版本化 prompt |
| `tagged_at` | datetime | LLM 调用完成时间 |
| `tagged_raw_response` | text (JSON) | **LLM 原 reply** ← 包含 reasoning，调试用 |

**唯一键约束**：`unique(clean_id, prompt_version, model)` —— 同一文本在不同 prompt/模型下可有多份标注，支持对比

### 表 5（辅助）：`pipeline_runs` （**可观测性**）

> **存什么**：每次跑 `python -m src.pipeline ...` 的元数据。

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `run_id` | int (PK) | 自增 |
| `platform` | str | steam / bilibili |
| `target_id` | str | |
| `started_at` / `ended_at` | datetime | 起止时间 |
| `fetched_count` | int | 拉到几条原始 |
| `inserted_count` | int | 新入库几条 |
| `analyzed_count` | int | LLM 分析成功几条 |
| `failed_count` | int | 失败几条 |
| `status` | str | success / partial / failed |
| `analyzer` | str | deepseek / qwen / glm / local |
| `triggered_by` | str | manual / cron / github_actions |

**作用**：让"上次跑失败了多少条""为什么近一周分析成功率下降"这种问题秒答。

---

## 👁 三、L4 视图层（不存表，只存 SQL 视图）

> 仪表盘不直接查单表，**全部通过 SQL View 拼接**。

```sql
-- 仪表盘主视图
CREATE VIEW v_comments AS
SELECT
    c.clean_id      AS id,
    c.target_id,
    t.name          AS target_name,
    t.type          AS target_type,
    c.platform,
    c.source_id,
    r.author_id,
    c.cleaned_text  AS content,
    c.language,
    c.is_valid,
    t.sentiment,
    t.sentiment_score,
    t.sentiment_confidence,
    t.topic,
    t.sub_topics,
    r.posted_at,
    r.likes,
    r.replies,
    r.raw_json,
    r.extra_json,
    r.fetched_at,
    t.tagged_at
FROM cleaned_comments c
JOIN tagged_comments  t ON c.clean_id = t.clean_id
JOIN raw_comments     r ON c.raw_id   = r.raw_id
LEFT JOIN targets     tg ON c.target_id = tg.target_id;
```

**为什么用视图**：
- 设计师对接 dashboard 时**只认一套字段**（这套就是字段前缀 A_/B_/C_ 的来源）
- 内部重构（例如把 L2 / L3 合并表）不影响前端
- 视图权限可单独控制（虽然个人项目用不上，但设计上是习惯）

---

## 📤 四、L5 导出层（一次性脚本 → 文件）

> 不是表，是 **`data/exports/`** 目录下的具体文件。

```
data/
├── voc.db                           # SQLite 主库
├── exports/
│   ├── cs2_50_20260731.csv         # ← 这次的导出物（带日期）
│   ├── cs2_50_20260731.json
│   ├── dota2_20260731.csv          # 明天可能导出 dota2
│   └── manifest.yaml               # 描述每个导出文件的元数据（可选）
```

**文件名约定**：`{target_slug}_{count}_{date}.{ext}`

**导出脚本策略**：
- 走视图层（L4）取数，不直接 SELECT 原表
- 同一份数据可以再导不同"主题筛选"版本（如 `cs2_50_20260731_topics_gameplay.csv`）
- 不放回数据库（避免污染业务表）

---

## 🔄 五、流水线（每次 `python -m src.pipeline ...` 怎么走）

```
[采集] → raw_comments
   ↓ 触发 L2
[清洗] → cleaned_comments
   ↓ 触发 L3
[标注] → tagged_comments
   ↓ 出
[导出视图 L4]
   ↓ 写入
[导出文件 L5]
```

每一步的**触发关系**：
- L1 → L2 是**幂等**的（同一个 raw_id 多次清洗可重写）
- L2 → L3 是**幂等**的（按 `clean_id + model + prompt_version` 唯一）
- L2 改了清洗规则 → 全量 re-clean L2 → 然后跑 L3 增量（不会重做 L1）

**异常处理**：
- L3 失败 → 写 `tagged_comments` 的"tagged_raw_response"记失败原因，可在仪表盘标"待复核"
- 单条 LLM 调失败不阻塞整批 → 打标 status = 'failed' 并继续

---

## 📊 六、数据量与性能基线（个人项目级）

| 量级 | 当前方案 | 备注 |
|---|---|---|
| **< 1 万条/月** | SQLite 完全够用 | 这就是 v0.1 |
| **5 万条/月** | 继续 SQLite，加索引 | 见下方索引建议 |
| **10 万条/月** | 切 PostgreSQL 单机 | SQLAlchemy 一行改 connect string |
| **50 万条/月** | 必须做归档（按月分库）| 太长远，不在 v0.1-v1.0 范围 |

**关键索引**（覆盖仪表盘高频查询）：
```sql
CREATE INDEX ix_raw_target ON raw_comments(target_id);
CREATE INDEX ix_clean_target ON cleaned_comments(target_id);
CREATE INDEX ix_tagged_target ON tagged_comments(target_id);
CREATE INDEX ix_tagged_sentiment ON tagged_comments(sentiment);
CREATE INDEX ix_raw_posted_at ON raw_comments(posted_at DESC);
```

---

## 🎨 七、与字段前缀约定的关系（重要）

> **[DATA_FIELDS.md](./DATA_FIELDS.md)** 里的字段前缀 `A_/B_/C_` 描述的是**逻辑层**而非物理列名。

| 字段前缀 | 物理列可能落在哪 |
|---|---|
| `A_` | `raw_comments`（原始 API 字段） |
| `B_` | `cleaned_comments` + `targets` 表里的元数据 / 派生 |
| `C_` | `tagged_comments`（LLM 标签） |
| (无前缀) | 在视图层 `v_comments` 里把 A/B/C 拼起来给 dashboard 用 |

所以**设计师看到的字段前缀是逻辑**、**工程师接触的是物理表 / 物理列**。两边约定是一致的：

- 设计师勾 "C_sentiment" → 仪表盘查 `tagged_comments.sentiment`
- 设计师勾 "A_content" → 视图拼接来自 `raw_comments.content`

---

## 🚚 八、从当前到目标架构的迁移路径（v0.2 起可分批做）

> **不会"重新开发"，是渐进式拆分**：保留表数据不动，按下面节奏扩展。

| 步骤 | 时间 | 改动 | 兼容性 |
|---|---|---|---|
| **M1：建视图** | 半天 | 在现有 `comments` 上建 `v_comments` 视图 | 已有仪表盘/导出脚本改查询路径即可 |
| **M2：拆 `targets` 表** | 半天 | 新建表 + 回填现有 `extra_meta` 数据 | 不影响业务逻辑 |
| **M3：新增 `raw_comments`** | 1 天 | 把原 `comments` 的原始字段复制过去；旧表标为 `legacy` | 历史数据完整保留 |
| **M4：新增 `cleaned_comments` + `tagged_comments`** | 1 天 | 把原 `comments` 的清洗和标签字段拆开 | 视图查询保持兼容 |
| **M5：上线 `pipeline_runs`** | 半天 | 每次 pipeline 跑完写一行 | 加可观测性 |
| **M6：废弃旧 `comments` 表** | 视情况 | 等所有消费者（仪表盘/导出）切到 v_comments | 重命名 `comments` → `legacy_comments` |

> ⏸ 注意：**这是可选的**，不是必须。如果你觉得当前 v0.1 的"一张表堆全部"够用，可继续；当遇到"改 prompt 不能重跑"或"想看历史标注对比"时，再启动 M2-M6。

---

## 📝 九、决策摘要（给设计师 / 产品 / 投资人看的简化版）

| 问题 | 答案 |
|---|---|
| 数据存在哪里？ | `data/voc.db` 单文件，SQLite |
| 多平台差异怎么存？ | 原始表里 `raw_json` + `extra_json` JSON 字段 |
| LLM 改了 prompt 怎么办？ | 原始 L1 表不动，重跑 L2/L3 即可 |
| 想对比不同模型标注？ | L3 表里 `model` 字段 + 唯一键 `(clean_id, model, prompt_version)` |
| 想分析数据怎么办？ | 直接读视图 `v_comments` 或 `data/exports/*.csv` |
| 数据库够用吗？ | 个人项目可用到 10W 条；5W 量级加索引；50W 量级切 PostgreSQL |
| 要看运行历史？ | `pipeline_runs` 表，每次跑自动写一行 |

---

## ⚠️ 十、当前架构的不足（诚实声明，避免过度设计）

- ❌ 还没有"用户系统"（个人项目不需要）
- ❌ 还没有"权限隔离"（任何 SQL 都被无限制执行）
- ❌ 还没有"自动化调度"（GitHub Actions 是未来的事）
- ❌ 还没有"增量采集"（重跑一次 pipeline = 全量）

这些都是**已知故意不做**的。等真有需要再加，不要当前实现。要做好这件事，时刻记得：这是个人项目，**学习价值 > 功能完整度**。
