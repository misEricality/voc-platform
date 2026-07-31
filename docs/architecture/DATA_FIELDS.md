# VoC 平台 · 数据字段说明

> **用途**：让设计师/产品/技术对"一条评论在我们数据库里到底有什么"有共同语言。
>
> **更新方式**：每次新建平台采集器时，增补对应的 A 类字段。

---

## 🏷️ 字段三级分类

按数据来源分三类，**字段名前缀即为类别标记**：

| 前缀 | 类别 | 含义 |
|---|---|---|
| **`A_`** | 🟢 **采样原始** | 平台 API 直接返回，没有加工（采集器照搬） |
| **`B_`** | 🟡 **程序派生** | 我们的采集/存储层补充、加工、分拆的字段 |
| **`C_`** | 🔵 **LLM 标注** | 由 DeepSeek / Qwen / GLM 等大模型分析后填入 |

> 设计师如果勾选字段勾错了来源，技术同学一眼就能定位是哪一层出的问题。

---

## 🟢 A. 采样原始字段（Steam 当前；后续 B 站/微博会补各自字段）

> 取自 `src/collectors/steam.py` 中 `_to_raw()` 方法。
> 字段命名严格对应 Steam Review API JSON 路径。

| 字段名 | 类型 | 来源 Steam 字段 | 示例 | 业务含义 |
|---|---|---|---|---|
| `A_source_id` | 字符串 | `recommendationid` | `"231636247"` | Steam 推荐唯一 ID（同平台下唯一） |
| `A_content` | 文本 | `review` | `"我认为该游戏可玩性高..."` | 评论正文（已去掉首尾空白） |
| `A_author_id` | 字符串 | `author.steamid` | `"76561198348085236"` | Steam 用户 Steam64 ID（仅脱敏 ID，无昵称） |
| `A_rating` | 整数 0/1 | `voted_up` | `1` | 1=推荐/好评；0=不推荐/差评 |
| `A_language` | 字符串 | `language` | `"schinese"` | 评论语种（schinese=简体中文） |
| `A_likes` | 整数 | `votes_up` | `1` | 评论被点赞数 |
| `A_replies` | 整数 | `comment_count` | `0` | 该评论下的回复数 |
| `A_posted_at` | 时间 | `timestamp_created` | `"2026-07-30T10:57:00"` | 评论发布时间（Steam 给的是 Unix 秒） |

**特别注意**：
- `A_author` 字段本来该是昵称，但因为合规要求（**不存用户隐私**），代码只存了 steamid（即作者匿名 ID）作为 `A_author_id`，没有昵称
- 字段集中于**可见文本 + 行为计数**两类，不涉及任何隐私字段
- 调用方可在入库前对 `A_content` 做脱敏 / 截断

---

## 🟡 B. 程序派生字段

> 取自 `src/storage/db.py` 的 `Comment` 模型 + `CommentRepository.upsert()`。

| 字段名 | 类型 | 衍生逻辑 | 示例 | 业务含义 |
|---|---|---|---|---|
| `B_id` | 整数 | SQLite 自增主键 | `1` | 数据库内唯一 ID |
| `B_platform` | 字符串 | 采集器硬编码 | `"steam"` | 数据来源平台（steam/bilibili/weibo） |
| `B_target_id` | 字符串 | `f"{platform}:{appid}"` | `"steam:730"` | 评论关联的目标实体唯一 ID |
| `B_target_meta` | JSON 字符串 | `extra_meta`：游戏元信息 | `{"name": "Counter-Strike 2", "type": "game"}` | 目标实体（游戏）的元数据，目前只存名称和类型 |
| `B_extra_json` | JSON 字符串 | `extra`：Steam 特有字段 | `{"appid": "730", "playtime_forever": 85435, "playtime_at_review": 85360, "steam_purchase": true, "received_for_free": false, "written_during_early_access": false}` | 平台专有数据，采集中会按平台补字段 |
| `B_fetched_at` | 时间 | 入库时刻（`_utcnow()`） | `"2026-07-31T14:35:53"` | 本次采集入库的时间 |

**B_extra_json 按平台的字段差异**：

| 平台 | 通常存什么 |
|---|---|
| steam | `appid`, `playtime_forever`, `playtime_at_review`, `steam_purchase`, `received_for_free`, `written_during_early_access` |
| bilibili | `aid`（视频ID）、`bvid`、`oid`（评论归属）|
| weibo | `weibo_id`、`user_mid`、`reposts_count` |

> 设计师做原型时，**平台特定字段**建议做动态展示（按平台标题分组）。如果只关心核心体验，B_id / B_target_id / B_target_meta 足够用于 P1 数据视图。

---

## 🔵 C. LLM 标注字段（DeepSeek 当前）

> 取自 `src/analyzers/sentiment_llm.py`，prompt 模板见 `USER_PROMPT_TEMPLATE`。
> 出自 `AnalysisResult` 数据类。

| 字段名 | 类型 | 模型输出形态 | 示例 | 业务含义 |
|---|---|---|---|---|
| `C_sentiment` | 字符串 | 三选一 | `"positive"` | 情感倾向：`positive` / `negative` / `neutral` |
| `C_sentiment_score` | 浮点 | -1.0 ~ +1.0 | `0.6` | 情感强弱（极负面 -1 → 极正面 +1） |
| `C_sentiment_confidence` | 浮点 | 0.0 ~ 1.0 | `0.8` | 模型对自己的判断有多大把握 |
| `C_topic` | 字符串 | 主标签 | `"游戏性"` | 评论核心讨论的一级主题 |
| `C_sub_topics` | JSON 字符串 | 子标签列表 | `'["可玩性", "操作"]'` | 主标签下的更细颗粒度子项 |
| `C_analyzed_at` | 时间 | 分析完成时刻 | `"2026-07-31T14:36:55"` | 何时分析完成 |

**尚未落库的潜在字段**（设计稿可以预留位置）：

| 字段名 | 类型 | 当前状态 | 备注 |
|---|---|---|---|
| `C_reasoning` | 文本 | 模型输出但**未存库** | 让模型解释"为什么这么判断"，对人工抽样审核很有用 |

---

## 📊 一个完整样本（来自 `data/exports/cs2_50_sample_5.json` 第 1 条）

```json
{
  "A_source_id": "231636247",
  "A_content": "我认为该游戏可玩性高，除了在网咖打游戏不能保留设置，枪法不固定。。。",
  "A_author_id": "76561198348085236",
  "A_rating": 1,
  "A_language": "schinese",
  "A_likes": 1,
  "A_replies": 0,
  "A_posted_at": "2026-07-30T10:57:00",
  "B_id": 1,
  "B_platform": "steam",
  "B_target_id": "steam:730",
  "B_target_meta": "{\"name\": \"Counter-Strike 2\", \"type\": \"game\"}",
  "B_extra_json": "{\"appid\": \"730\", \"playtime_forever\": 85435, ..., \"written_during_early_access\": false}",
  "B_fetched_at": "2026-07-31T14:35:53.312259",
  "C_sentiment": "positive",
  "C_sentiment_score": 0.6,
  "C_sentiment_confidence": 0.8,
  "C_topic": "游戏性",
  "C_sub_topics": "[\"可玩性\", \"操作\"]",
  "C_analyzed_at": "2026-07-31T14:36:55.419666"
}
```

> 一句话翻译：**第 1 条评论**是 **Counter-Strike 2**（游戏）的一条**简体中文好评**，Steam ID 是 `765611...`，玩家玩了 **85435 分钟 ≈ 1423 小时**，玩家对游戏**整体正面**（强度+0.6，置信度 0.8），主题属"游戏性"（子标签：可玩性 / 操作），DeepSeek 在 2026-07-31 14:36:55 完成标注。

---

## 📦 导出物清单

| 路径 | 内容 |
|---|---|
| `data/exports/cs2_50_full.json` | 50 条 JSON 全量明细 |
| `data/exports/cs2_50_full.csv` | 50 条 CSV（Excel 直接打开，UTF-8 BOM） |
| `data/exports/cs2_50_sample_5.json` | 前 5 条示例，更易读 |
| `scripts/export_cs2.py` | 一次性导出脚本，可重跑 |

---

## ⚖️ 字段主权清单（给设计师选型时参考）

| 用例 | 可用字段 |
|---|---|
| 评论筛选器（按情感） | `C_sentiment`, `C_sentiment_score` 阈值 |
| 评论筛选器（按主题） | `C_topic`, `C_sub_topics`（注意 sub_topics 是 JSON 列表） |
| 评论筛选器（按平台） | `B_platform`（steam/bilibili/weibo） |
| 评论筛选器（按目标） | `B_target_id` 或 `B_target_meta.name` |
| 评论筛选器（按时间） | `A_posted_at` |
| 卡片视图 | `A_content` + `A_rating` + `C_sentiment` + `C_topic` |
| 列表视图 | 全部 A 类 + 摘要性 C 类 |
| 时间趋势图 | 按 `A_posted_at` 维度聚合 `C_sentiment` |
| 主题分布图 | `C_topic` TOP N |
| 词云 | `A_content` 文本经过 jieba 分词 |
| 置信度筛选 | `C_sentiment_confidence` 阈值（推荐 0.6 以上显示） |
| 数据来源标注 | `B_platform` 显示 + `B_extra_json` 按平台差异化展开 |
| 重新分析触发 | 后端逻辑字段，**不显示在原型** |
| 平台特有字段 | 按平台动态展示 `B_extra_json` 不同键值 |

---

## 🚧 已知字段缺口（v0.2 候选）

| 缺口 | 影响 | 优先级 |
|---|---|---|
| `C_reasoning` 没有存库 | 设计师想做"AI 解释"卡片时会缺数据 | 🟡 中 |
| `A_author` 昵称字段缺失 | 国内用户不熟悉 Steam64 ID，列表页看起来冷 | 🟡 中（注意隐私边界） |
| 评论原文截断长度 | 当前 200 字符上限可能丢失长评的情感 | 🟢 低 |
| 多语言字段 | 当前只有 `A_language`，没有语义化"已翻译"的字段 | 🟢 低 |

---

> 💡 **设计师对接建议**：原型图先区分三类卡片样式：
> - **原始卡片**：A 类 + B_id（用于辨识）
> - **标注卡片**：C 类（用于洞察维度筛选）
> - **平台特色卡片**：B_extra_json 按平台展开（用于差异化体验）
>
> 可以直接拿 `data/exports/cs2_50_full.csv` 进 Excel / Figma 看到完整数据形态。
