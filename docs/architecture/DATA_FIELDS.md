# 灵听 · Lynx · 数据字段说明

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
| **`D_`** | 🔴 **模型派生** | 本地 embedding 模型输出（语义向量，衍生数据可重建） |

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
| `A_likes` | 整数 | `votes_up` | `1` | 评论被点赞数（v0.2 起冷启动为 `NULL`=未回采，7 天后回采填充） |
| `A_replies` | 整数 | `comment_count` | `0` | 该评论下的回复数（冷启动语义同 `A_likes`） |
| `A_posted_at` | 时间 | `timestamp_created` | `"2026-07-30T10:57:00"` | 评论发布时间（Steam 给的是 Unix 秒；统一落库为 **naive UTC**，与 `fetched_at`/`refreshed_at` 口径一致） |

**特别注意**：
- `A_author` 字段本来该是昵称，但因为合规要求（**不存用户隐私**），代码只存了 steamid（即作者匿名 ID）作为 `A_author_id`，没有昵称
- 字段集中于**可见文本 + 行为计数**两类，不涉及任何隐私字段
- 调用方可在入库前对 `A_content` 做脱敏 / 截断

---

## 🟢 A. 采样原始字段（Bilibili · 2026-08-13 新增）

> 取自 `src/collectors/bilibili.py` 中 `_to_raw()` 方法。
> 采集定位：发布满 7 天的「口碑稳态快照」；评论抽样 K=1000（点赞 top-600 + 最新 400）。
> 规格依据：`BILIBILI_COLLECTION.md`。

| 字段名 | 类型 | 来源 reply 接口 | 示例 | 业务含义 |
|---|---|---|---|---|
| `A_source_id` | 字符串 | `rpid` | `"252492046737"` | B 站评论唯一 ID |
| `A_content` | 文本 | `content.message` | `"大师兄到底是多绝望..."` | 评论正文 |
| `A_author_id` | 字符串 | `member.mid` | `"336610383"` | B 站用户 mid |
| `A_author` | 字符串 | `member.uname` | `"落花影I"` | 昵称（B 站公开数据，可存） |
| `A_likes` | 整数 | `like` | `30483` | 点赞数（快照模式直接存实值，无 7 天回采语义） |
| `A_replies` | 整数 | `rcount` | `109` | 楼中楼回复数 |
| `A_posted_at` | 时间 | `ctime` | `"2025-01-22T12:07:58"` | 评论时间（统一落库为 **naive UTC**） |
| `A_profile` | JSON | `member` 派生 | `{"uname":"落花影I","level":6,...}` | 评论者画像（存 `extra_json.profile`） |

**特别注意（实测校准 2026-08-13）**：
- `A_profile.level` 取自 `member.level_info.current_level`（非顶层 `level`，顶层实测为 None）
- `A_profile.official` 取自 `member.official_verify`（role/title/desc）
- **热门视频评论必须登录 cookie（SESSDATA）**：匿名访问只返回第 1 页 3 条（风控降级）——BV1UpwaeNESx（4.6 万评论）实测；SESSDATA 配在 `.env`（BILIBILI_SESSDATA）
- 弹幕只落 `user_hash`（接口匿名 hash），不存真实身份（合规）

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
| `B_likes_refreshed_at` | 时间 | 回采脚本写入 | `"2026-08-07T09:00:00"` | 点赞/回复/开发者回复最近一次回采时间（v0.2 新增） |
| `B_developer_response_refreshed_at` | 时间 | 回采脚本写入 | `"2026-08-07T09:00:00"` | 开发者回复最近一次回采时间（v0.2 新增） |

**B_extra_json 按平台的字段差异**：

| 平台 | 通常存什么 |
|---|---|
| steam | `appid`, `playtime_forever`, `playtime_at_review`, `steam_purchase`, `received_for_free`, `written_during_early_access` |
| bilibili | `aid`（视频ID）+ `profile`（评论者画像：level/vip/sex/official） |
| weibo | `weibo_id`、`user_mid`、`reposts_count` |

> 设计师做原型时，**平台特定字段**建议做动态展示（按平台标题分组）。如果只关心核心体验，B_id / B_target_id / B_target_meta 足够用于 P1 数据视图。

---

## 🔵 C. LLM 标注字段（DeepSeek 当前）

> 取自 `src/analyzers/sentiment_llm.py` 与 `src/analyzers/base.py`（`AnalysisResult` / `Opinion` 数据类）。
> 方案4（2026-08-06）后：**LLM 只提取观点短语**，标签由程序匹配，`C_topic` 由程序从核心观点映射 L1。
> 完整流程见 [ANNOTATION_PIPELINE.md](./ANNOTATION_PIPELINE.md)。

| 字段名 | 类型 | 来源 | 示例 | 业务含义 |
|---|---|---|---|---|
| `C_sentiment` | 字符串 | 核心观点情感 | `"positive"` | 整体情感：`positive` / `negative` / `neutral`（= 核心观点 is_core 的情感） |
| `C_sentiment_score` | 浮点 | 核心观点分数 | `0.6` | 情感强弱（极负面 -1 → 极正面 +1） |
| `C_sentiment_confidence` | 浮点 | 模型输出 | `0.8` | 模型对自己的判断有多大把握 |
| `C_topic` | 字符串 | **程序映射** | `"机制与内容"` | 核心观点匹配到的 **L1**（`normalize.map_l3_to_path`，GDT v3.1.1） |
| `C_sub_topics` | JSON 字符串 | 兼容保留 | `'["可玩性", "操作"]'` | v0.1 遗留列，方案4 后由 `comment_opinions` 表替代，不新增写入；`export_xlsx.py` 已不再导出 |
| `C_analyzed_at` | 时间 | 分析完成时刻 | `"2026-08-06T21:00:00"` | 何时分析完成 |

### 观点级标注（comment_opinions 表）

> 方案4 新增：每条评论可拆出多个观点（每观点一行，跨多个 L1 不限）。

| 字段名 | 类型 | 含义 |
|---|---|---|
| `full_path` | 字符串 | 完整路径（`"机制与内容/核心机制与循环/战斗系统"`，L1/L2/L3 三段不留空） |
| `sentiment` | 字符串 | 观点级情感（positive/negative/neutral） |
| `sentiment_confidence` | 浮点 | 观点级置信度 |
| `quote` | 文本 | 观点短语（LLM 从原声提取） |
| `quote_start` / `quote_end` | 整数 | 观点短语在原声中的字符位置（可选） |
| `comment_id` | 整数 | 外键 → `comments.id` |

**尚未落库的潜在字段**（设计稿可以预留位置）：

| 字段名 | 类型 | 当前状态 | 备注 |
|---|---|---|---|
| `C_reasoning` | 文本 | 模型输出但**未存库** | 让模型解释"为什么这么判断"，对人工抽样审核很有用 |

---

## 🔴 D. 模型派生字段（本地语义向量，2026-08-11 新增）

> 取自 `src/storage/db.py` 的 `CommentEmbedding` 模型 + `src/analyzers/embedder.py`。
> 用途：语义检索 / 聚类（"其他"治理）/ 观点去重聚合。
>
> **关键属性：衍生数据（derived data）**——向量可由 `A_content` 随时全量重建，
> 因此换模型 = 清表重算（`scripts/ops/backfill_embeddings.py --force`），
> 不保留旧向量。存储 L2 归一化后的 float32 数组（内积 = 余弦相似度）。

| 字段名 | 类型 | 衍生逻辑 | 示例 | 业务含义 |
|---|---|---|---|---|
| `D_embedding_comment_id` | 整数 | 1:1 → `comments.id` | `1` | 向量归属的评论 |
| `D_embedding_model` | 字符串 | 编码器标识（含小版本号） | `"BAAI/bge-small-zh-v1.5"` | **强制记录**：换模型后新旧向量空间不可比，禁止混存（单空间约束） |
| `D_embedding_dim` | 整数 | 模型维度 | `512` | 向量维度 |
| `D_embedding_vector` | BLOB | `np.float32.tobytes()` | — | 归一化向量本体（512×4B≈2KB/条） |
| `D_embedding_created_at` | 时间 | 生成时刻 | `"2026-08-11T21:40:24"` | 何时编码 |

**单空间约束（三防线）**：
1. 写入侧（pipeline）：表内已有其他模型 → 跳过向量化并告警（不混写）；
2. 迁移侧（backfill）：`--force` 全量重算，单事务 DELETE+INSERT，崩溃回滚旧向量保留；
3. 读取侧（semantic_search / 聚类）：检索前断言 `COUNT(DISTINCT model) = 1`，混合空间拒绝执行。

---

> ⚠️ 以下样本为 **v0.1 导出格式快照**（`data/exports/cs2_50_sample_5.json`，含 `C_sub_topics`）。方案4 后分析结果为 `comments` + `comment_opinions` 双表，字段语义见上文 C 类节。
>
> 一个完整样本（v0.1 格式）：

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
| 语义检索（AI） | `D_embedding_*`（`src.analyzers.embedder.semantic_search`） |
| 语义聚类（"其他"治理） | `D_embedding_vector` + 余弦距离 |
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

## 📺 B 站弹幕表（danmaku · 2026-08-13 新增）

> B 站独有资产：观看当下的即时情绪，带视频内时间戳。规格：`BILIBILI_COLLECTION.md` 3.3 节。

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `id` | 整数 | 自增 | 主键 |
| `video_id` | 字符串 | 派生 | `bilibili:video:{aid}`，对齐 `B_target_id` |
| `cid` | 字符串 | view 接口 | 分 P 弹幕池 id |
| `content` | 文本 | `list.so` XML | 弹幕文本 |
| `progress` | 整数 | `p` 属性第 1 段 | **视频内时间点（秒）**——情绪-内容时间轴 |
| `mode` | 整数 | `p` 属性第 2 段 | 弹幕类型（1=滚动 4=底部 5=顶部 7=高级） |
| `color` | 整数 | `p` 属性第 4 段 | 弹幕颜色（可作情绪粗信号） |
| `user_hash` | 字符串 | `p` 属性第 7 段 | 用户匿名 hash（不落真实身份） |
| `posted_at` | 时间 | `p` 属性第 5 段 | 弹幕发送时间（与 progress 双时间戳；落库为 **naive UTC**） |
| `fetched_at` | 时间 | 入库时刻 | 采集时间 |

**实测结论（2026-08-13）**：
- `x/v1/dm/list.so` 返回的是 B 站**防抖抽稀**后的代表性弹幕（实测 ~1200 条，progress 覆盖全程 0-357s 均匀分布），天然符合"弹幕永远抽样"策略，无需额外分片
- **编码坑**：必须 `content.decode('utf-8')` 解析，`r.text` 会因响应头缺 charset 按 latin-1 解码产生乱码
- 弹幕**不进 LLM 打标链路**（成本红线）：用词典匹配 + 时间窗聚合生成"情绪曲线"辅助信号

---

> 💡 **设计师对接建议**：原型图先区分三类卡片样式：
> - **原始卡片**：A 类 + B_id（用于辨识）
> - **标注卡片**：C 类（用于洞察维度筛选）
> - **平台特色卡片**：B_extra_json 按平台展开（用于差异化体验）
>
> 可以直接拿 `data/exports/cs2_50_full.csv` 进 Excel / Figma 看到完整数据形态。
