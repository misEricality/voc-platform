# Steam API 字段大全（VoC 项目权威字段清单）

> **用途**：列出本项目当前使用的 Steam 官方 Web API 的**全部可获取字段**及其含义。所有字段都经过实拉实测验证。
>
> **最后更新**：2026-08-01
> **关联文档**：
> - 字段三级分类约定：[DATA_FIELDS.md](./DATA_FIELDS.md)
> - 数据库分层架构：[DATA_STORAGE_DESIGN.md](./DATA_STORAGE_DESIGN.md)

---

## 🎯 给设计师对接的「字段使用场景」速查

> 这一节是**给设计师的第一道门**：拿到字段名 + 含义 → 直接配到原型图里。
> 后面章节是字段的权威字典（字段名 → 类型 → 来源 → 何时写入），仅在需要详细看时回来查。

### 🔰 列表必备字段（贯穿所有评论卡片）

> **当前评论所属的游戏名称**，固定位置，常规字体（不要加重）。当前项目只有一款游戏（CS2），未来扩展后会显得更必要。

| 字段 | 推荐字段含义 | 备注 |
|---|---|---|
| `B_target_meta.name` | 游戏名称（如「Counter-Strike 2」） | 取自 `extra_meta` 字段里的 `name` key |

---

### 数据采集生命周期（设计师先看这里）

| 阶段 | 发生时间 | 评论状态 | likes / replies 含义 | developer_response 含义 |
|---|---|---|---|---|
| **首次采集** | 评论发布后 0-7 天内 | `likes_refreshed_at` = NULL | 点赞数 = **未知**（系统刻意不存，避免冷启动 0 误导） | 回复 = **未知**（同理，开发者可能晚回） |
| **回采** | 评论发布满 7 天后 | `likes_refreshed_at` = 回采时刻 | 点赞数 = **真实值**（回采后写入） | 回复 = **真实值**（开发者后期回复能被捕获） |
| **永久** | — | `sentiment / topic` 等 C 类字段 | LLM 一次性标注后稳定不变 | — |

> ⚠️ **不要**把"0 点赞"和"未回采"混在一起。仪表盘需要能区分这两者。

### 推荐字段清单（按设计场景）

#### 💬 评论卡片

| 推荐字段 | 推荐字段含义 | 备注 |
|---|---|---|
| `review` | 评论正文 | 卡片主体 |
| `voted_up` | true=推荐 / false=不推荐（Steam 玩家投票） | 头像左下角徽标 |
| `timestamp_created` | 评论创建时间（Unix 秒） | **绝对时间（北京时区 YYYY-MM-DD HH:mm），不显示相对时间** |
| `votes_up` | 评论被点赞数 | 评论右侧"赞"图标旁数字 —— **注意：只有 7 天后回采的真实值；首次 7 天内显示「未知」** |
| `comment_count` | 该评论下的回帖数 | 评论右侧"评论"图标旁数字 —— **同理：只有 7 天后回采的真实值** |
| ~~`language`~~ | ~~多语言过滤~~ | ❌ **不展示** |

#### 🎮 游戏信息（在卡片上方/下方显著位置）

| 推荐字段 | 推荐字段含义 | 备注 |
|---|---|---|
| `B_target_meta.name` | 游戏名称 | **必备**，列表样式贯穿 |

#### 💰 购买渠道分析

| 推荐字段 | 推荐字段含义 | 备注 |
|---|---|---|
| `received_for_free` | true=免费获得（礼物/活动/限免） | 展示 |
| `refunded` | true=已退款 | **仅 true 时醒目展示**（如红色徽章） |
| ~~`steam_purchase`~~ | ~~Steam 商店付费购买~~ | ❌ **不展示** |

#### 🆚 版本对比

| 推荐字段 | 推荐字段含义 | 备注 |
|---|---|---|
| `written_during_early_access` | true=抢先体验期间发布 | **仅 true 时醒目展示**（如黄色徽章），**与 refunded 样式不同**（颜色区分） |
| `primarily_steam_deck` | true=主要在 Steam Deck 玩 | **仅 true 时展示**，**不用醒目样式**（普通小标签即可） |
| ~~`deck_playtime_at_review`~~ | ~~Deck 上的游戏时长~~ | ❌ **不展示** |

#### ⏱ 玩家活跃度

| 推荐字段 | 推荐字段含义 | 备注 |
|---|---|---|
| `author.playtime_at_review` | 写评论时的游戏时长（分钟） | 展示 |
| ~~`author.playtime_forever`~~ / ~~`playtime_last_two_weeks`~~ / ~~`last_played`~~ | | ❌ **其他不展示** |

#### ⭐ 有用性排序

| 推荐字段 | 推荐字段含义 | 备注 |
|---|---|---|
| `weighted_vote_score` | Steam 内部贝叶斯平均（0-1） | **醒目展示分数**（如星级或显著数字），**不做排序** |

#### 🏛 官方态度信号

| 推荐字段 | 推荐字段含义 | 备注 |
|---|---|---|
| `developer_response` | 开发者回复文本 | **展开显示**：在原评论下用「楼中楼」样式展示（**比评论正文字体更小**），**可折叠**，**默认展开** |
| `timestamp_dev_responded` | 开发者回复时间戳 | 同上样式，跟随回复文本一起展示 |

> ⏰ **采集机制**：`developer_response` 与 `votes_up` / `comment_count` **同机制** —— 首次采集时存为 NULL（避免 0 误导），**评论发布满 7 天后通过回采脚本（`scripts/ops/refresh_likes.py`）拉取真实回复**。仪表盘需要区分"未回采"与"开发者真的没回复"两种状态。

#### 🔒 隐私过滤

| 推荐字段 | 推荐字段含义 | 备注 |
|---|---|---|
| `author.steamid` | Steam64 ID（17 位数字） | **永远只展示后 4 位**，防爬虫反向追踪 |
| ~~`author.personaname`~~ | 昵称 | ❌ **项目不存该字段** |

---

### 🚫 不展示的字段（出于产品决策）

| 设计用例 | 字段 | 不展示原因 |
|---|---|---|
| 玩家画像 | `author.num_games_owned` / `author.num_reviews` / `author.playtime_forever` | 产品决策：本版本不展示玩家画像 |
| 整盘口碑 KPI | `query_summary.review_score_desc` / `total_positive` / `total_negative` / `review_score` | 当前只有一款游戏，**待「游戏列表」开发时启用** |
| 趋势分析 | `app_release_date` / `timestamp_created`（聚合维度） | 同上，待「游戏列表」开发时启用 |
| 平台特有功能 | `reactions` | Steam 未来启用反应表情时再展示 |
| 未来功能预留 | `votes_funny` | 基本没人用 |
| 设备画像 | `deck_playtime_at_review` / `playtime_last_two_weeks` / `last_played` | 产品决策：不展示 |

> 💡 「整盘口碑 KPI」与「趋势分析」等被标注为「待游戏列表开发时启用」—— 等仪表盘支持多游戏横向对比时，这些字段会有强展示价值。

---

### 哪些字段在「什么时候」会出现？（数据生命周期）

| 字段 | 首次入库 | 7 天后回采 | 备注 |
|---|---|---|---|
| `review` (评论正文) | ✅ | — | 不可变 |
| `voted_up` / `rating` | ✅ | — | 不可变 |
| `timestamp_created` | ✅ | — | 不可变 |
| `author.steamid` | ✅ | — | 不可变 |
| `author.playtime_at_review` | ✅ | — | 不可变 |
| `received_for_free` / `written_during_early_access` / `refunded` / `primarily_steam_deck` | ✅ | — | 不可变 |
| `weighted_vote_score` | ✅ | — | 不变 |
| `developer_response` | ❌ **NULL** | ✅ **真实值** | **业务规则：7 天后回采（与 likes 同机制）** |
| `timestamp_dev_responded` | ❌ **NULL** | ✅ **真实值** | 同上 |
| `B_target_meta.name` | ✅ | — | 首次采集时回填 |
| **`votes_up` (点赞)** | ❌ **NULL** | ✅ **真实值** | **业务规则：7 天后回采** |
| **`comment_count` (回帖)** | ❌ **NULL** | ✅ **真实值** | **业务规则：7 天后回采** |

> 仪表盘设计时刻表：根据"评论发布时间 + 7 天"判断"未回采 vs 真实无点赞"。

---

## 📋 总览：3 个 Steam 接口 + 1 个聚合视图

| 接口 | 路径 | 用途 | 频率 | 是否需 Key |
|---|---|---|---|---|
| **1. Reviews** | `store.steampowered.com/appreviews/<appid>` | 拉取评论正文 + 情感标注 + 玩家元数据 | 高频（每天） | ❌ 不强制 |
| **2. App Details** | `store.steampowered.com/api/appdetails?appids=<appid>` | 拉取游戏/视频元信息（名称、价格、类型） | 低频（每次见新 appid） | ❌ 不强制 |
| 3. AppList（未启用） | `api.steampowered.com/ISteamApps/GetAppList/v2/` | 枚举所有 Steam 应用 | 偶尔 | ⚠️ 需要 Key |

---

## 🟢 2. Reviews API（`GET /appreviews/<appid>`）

> 我们的核心采集目标。

### 2.1 顶层响应（每次调用）

```jsonc
{
  "success": 1,                        // 1=成功 0=失败
  "query_summary": { ... },            // 当次请求的统计（见 2.2）
  "reviews": [ ... ],                  // 评论数组（见 2.3）
  "cursor": "AoIFQFZh..."              // 下次请求的 cursor，首次传 "*"
}
```

**我们的代码（`src/collectors/steam.py`）当前怎么用：**
- `cursor = "*"` → 第一次调用
- `reviews[i].recommendationid` → 写入 `source_id`
- `data.get("cursor")` → 给下次请求
- `query_summary.num_reviews` → 当次拉到的条数

---

### 2.2 `query_summary` 字段（请求级聚合）

| 字段 | 类型 | 含义 |
|---|---|---|
| `num_reviews` | int | **本次响应里返回的评论数**（不是全平台总量） |
| `review_score` | int | 当前**好评率**的 Steam 内部分数（0-10，越高越好），CS2 = 8 |
| `review_score_desc` | str | Steam 给的好评标签：`"Overwhelmingly Positive"` / `"Very Positive"` / `"Positive"` / `"Mostly Positive"` / `"Mixed"` / `"Mostly Negative"` / `"Negative"` / `"Very Negative"` / `"Overwhelmingly Negative"` |
| `total_positive` | int | 平台累计好评数（CS2 = 1,250,460） |
| `total_negative` | int | 平台累计差评数（CS2 = 153,151） |
| `total_reviews` | int | 平台累计评论总数（CS2 = 1,403,611） |

**业务价值**：这是一个"数据皇冠"——是 Steam 官方计算出的整盘口碑。可以做"CS2 vs DOTA2 整盘口碑"对比图，是少有的、**不用 LLM 也能拿到的可信信号**。

**当前状态**：❌ 我们没存进 DB，存进 `targets.extra_json` 即可。

---

### 2.3 `reviews[]` 单条评论字段（核心字段集合 · 权威）

> 实拉真实接口（CS2 中文区）核对，包含官方文档 + 实测新发现。

#### 2.3.1 评论主体字段

| 字段 | 类型 | 我们的 prefix | 含义 |
|---|---|---|---|
| `recommendationid` | str | `A_source_id` | Steam 推荐唯一 ID（9-10 位数字字符串） |
| `review` | str | `A_content` | 评论正文（可能有 HTML 实体，已 trim） |
| `language` | str | `A_language` | 评论语种（`schinese` / `english` 等） |
| `timestamp_created` | int | → `A_posted_at` | 评论创建时间（**Unix 秒**） |
| `timestamp_updated` | int | — | 评论最近一次编辑时间（同上格式） |
| `voted_up` | bool | → `A_rating` (1/0) | **true = 推荐/好评**，false = 不推荐/差评 |
| `votes_up` | int | `A_likes` ← **回采时填** | 该评论**被点赞数**（其他玩家觉得这有帮助） |
| `votes_funny` | int | — | **"觉得搞笑"票数**（Steam 老功能，很少有人用） |
| `weighted_vote_score` | float (str) | B_extra_json | Steam 内部"有用性"加权分（0-1），用于 ranking |
| `comment_count` | int | `A_replies` ← **回采时填** | 该评论下的回帖数 |
| `steam_purchase` | bool | B_extra_json | **true = 该用户通过 Steam 商店付费购买** |
| `received_for_free` | bool | B_extra_json | **true = 用户勾选了"我免费获得的"**（礼物/活动/限免） |
| `refunded` | bool *(实测)* | B_extra_json | **true = 已退款** ← 官方文档没写但实测有！对"退款 = 不推荐"的关联分析很有价值 |
| `written_during_early_access` | bool | B_extra_json | **true = 在抢先体验期间写的**，上架前的评价往往更准 |
| `app_release_date` | str *(实测)* | B_extra_json | **游戏的发行时间戳**（所有评论共享此值，对同款游戏冗余） |
| `reactions` | list *(实测)* | B_extra_json | 评论"反应表情"，通常空列表 —— 潜在的新功能预留字段 |
| `primarily_steam_deck` | bool | B_extra_json | **true = 写评论时主要在 Steam Deck 上玩**，掌机用户画像 |
| `developer_response` | str | B_extra_json | **官方开发者回复的文本**（如有）—— 区分"开发者回应了差评"很有故事性 |
| `timestamp_dev_responded` | int | B_extra_json | 开发者回复时间戳 |

#### 2.3.2 `author` 子对象（11 个字段）

| 字段 | 类型 | 我们的 prefix | 含义 |
|---|---|---|---|
| `steamid` | str | `A_author_id` | Steam64 ID（全球唯一 17 位数字） |
| `personaname` | str *(实测)* | — ⚠️ 隐私 | **Steam 用户的公开昵称**（如 `"123456"`、`"红尘旧梦iW"`）。**注意是隐私风险 —— 项目目前不存** |
| `persona_status` | str *(实测)* | — ⚠️ 隐私 | 用户在线状态（`online` / `offline` / `away` 等） |
| `profile_url` | str *(实测)* | — ⚠️ 隐私 | Steam 个人主页 URL（拼在 `https://steamcommunity.com/profiles/` 后） |
| `avatar` | str *(实测)* | — ⚠️ 隐私 | 头像图片哈希，可拼出头像 CDN URL |
| `num_games_owned` | int | B_extra_json | 该用户**拥有的游戏总数**（鉴别重度玩家 vs 路人的关键指标） |
| `num_reviews` | int | B_extra_json | 该用户**历史发布过的评论总数**（鉴别核心玩家） |
| `playtime_forever` | int | B_extra_json (hours/60) | 该游戏**终身游戏时长（分钟）** |
| `playtime_last_two_weeks` | int | B_extra_json | **过去 14 天**游戏时长（鉴别"最近活跃" vs "云玩家") |
| `playtime_at_review` | int | B_extra_json | **写评论那一刻的游戏时长**（"已玩了 X 小时"） |
| `last_played` | int | B_extra_json | 最近一次玩游戏的时间戳（Steam Deck 有此字段） |
| `deck_playtime_at_review` | int *(实测)* | B_extra_json | **写评论时在 Steam Deck 上的游戏时长**（Deck 用户专项） |

#### 2.3.3 ⚠️ 隐私与合规边界

> **作者隐私字段（personaname / persona_status / profile_url / avatar）** —— Steam 评审政策里认定这些是公开信息，但**实际很多用户不喜欢被爬取**。**项目当前的设计决策是只存 steamid，不存昵称/头像/主页**。如未来采集 B 站 / 微博，这些字段需要更严格的合规审查（个别要 backlist 隐私安全 IP）。

#### 2.3.4 实测 vs 官方文档差异（重要！）

> 实测响应**比 Steam 官方文档多了 5 个字段**，建议信任实测！

| 字段 | 文档出处 | 实测出处 |
|---|---|---|
| `recommendationid`, `review`, `timestamp_created`, `timestamp_updated`, `voted_up`, `votes_up`, `votes_funny`, `weighted_vote_score`, `comment_count`, `steam_purchase`, `received_for_free`, `written_during_early_access`, `developer_response`, `timestamp_dev_responded`, `primarily_steam_deck` | ✅ Steam 官方 | ✅ 命中 |
| **`refunded`** | ❌ 文档没列 | ✅ 实测有 |
| **`app_release_date`** | ❌ 文档没列 | ✅ 实测有 |
| **`reactions`** | ❌ 文档没列 | ✅ 实测有（空列表） |
| **`personaname`** / **`persona_status`** / **`profile_url`** / **`avatar`**（author.*） | ❌ 文档没列 | ✅ 实测有 |
| **`deck_playtime_at_review`** | ❌ 文档没列 | ✅ 实测有 |
| **总计** | ~20 个 | **~28 个** |

---

## 🟡 3. App Details API（`GET /api/appdetails?appids=...`）

> 当前项目用于**回填游戏元数据**（名称、是否收费、类型）。

### 3.1 顶层结构

```jsonc
{
  "<appid>": {                       // 注意：appid 是顶层 wrapper 的 key
    "success": 1,
    "data": { ... }                  // 真正的元数据对象
  }
}
```

### 3.2 `data` 顶级字段（34 个）

| 字段 | 类型 | 业务含义 | 我们当前如何存 |
|---|---|---|---|
| `type` | str | `game` / `dlc` / `demo` / `mod` / `video` / `hardware` / `music` | ✅ 在 `targets.type` |
| `name` | str | 中文名（如 "Counter-Strike 2"） | ✅ 在 `targets.name` |
| `steam_appid` | int | Steam 给的 appid（恒等于请求的 appid） | ❌ 冗余不存（target_id 已含） |
| `is_free` | bool | true = 免费；false = 收费 | ❌ 未存 |
| `required_age` | int | 最小年龄 | ❌ 未存 |
| `developers` | list[str] | 开发商列表 | ❌ 未存 |
| `publishers` | list[str] | 发行商列表 | ❌ 未存 |
| `categories` | list[{id, description}] | 类别（如"多人""单人""竞技"） | ❌ 未存 |
| `genres` | list[{id, description}] | 类型（如"动作""FPS""策略"） | ❌ 未存 |
| `platforms` | {windows, mac, linux} | 各平台支持 | ❌ 未存 |
| `release_date` | {coming_soon, date} | 发售日期（中文格式字符串） | ❌ 未存 |
| `short_description` | str | 一句话简介 | ❌ 未存 |
| `detailed_description` | str | 详细 HTML 简介 | ❌ 未存 |
| `about_the_game` | str | 同上（Steam 双字段冗余） | ❌ 未存 |
| `supported_languages` | str | 支持语种（HTML 标记） | ❌ 未存 |
| `header_image` | str | 头图 URL | ❌ 未存 |
| `background` | str / url | 背景图 URL | ❌ 未存 |
| `background_raw` | str | 高分屏背景图 URL | ❌ 未存 |
| `capsule_image` | str | 小图 URL（页面列表用） | ❌ 未存 |
| `capsule_imagev5` | str | 同上 v5 版 | ❌ 未存 |
| `screenshots` | list[{id, path_thumbnail, path_full}] | 截图（产品图） | ❌ 未存 |
| `movies` | list[{id, name, thumbnail, dash_av1, dash_h264, hls_h264}] | 宣传视频（多种编码） | ❌ 未存 |
| `website` | str | 官方网站 URL | ❌ 未存 |
| `support_info` | {url, email} | Steam 客服/支持链接 | ❌ 未存 |
| `ratings` | dict | 多国分级（usk / agcom / cadpa / dejus / steam_germany / igrs / steam_australia） | ❌ 未存 |
| `content_descriptors` | {ids, notes} | 内容警示（如"含血腥画面"） | ❌ 未存 |
| `recommendations` | {total: int} | Steam **推荐量**（比 reviews 更早的字段，部分游戏可能没有） | ❌ 未存 |
| `achievements` | {total, highlighted: list} | 成就总量与精选 | ❌ 未存 |
| `pc_requirements` | {minimum, recommended?} | Windows 配置要求 | ❌ 未存 |
| `mac_requirements` | {minimum, recommended?} | macOS 配置要求 | ❌ 未存 |
| `linux_requirements` | {minimum, recommended?} | Linux 配置要求 | ❌ 未存 |
| `dlc` | list[int] | 本游戏的 DLC appid 列表 | ❌ 未存 |
| `packages` | list[int] | 包裹包 ID（购买组合） | ❌ 未存 |
| `package_groups` | list[...] | 购买组合定义 | ❌ 未存 |

### 3.3 价格详情（当 `is_free = false` 时出现）

我们当前没存价格字段。`appdetails` 还有嵌套字段：

```jsonc
"price_overview": {
  "currency": "CNY",
  "initial": 13800,            // 原价（分）
  "final": 6900,               // 现价（分）
  "discount_percent": 50,      // 折扣百分比
  "initial_formatted": "¥138.00",
  "final_formatted": "¥69.00"
}
```

---

## 🔵 4. AppList API（未启用，预留）

> `GET api.steampowered.com/ISteamApps/GetAppList/v2/`
> 用途：拿到全 Steam 应用清单 + appid 映射，**做"全游戏监测"功能时**才会用到。
> 需要 API Key。**目前不接**。

---

## 🧭 5. 当前采集器 vs 全字段对照（一目了然）

| 字段类别 | 总数 | 我们已存 | 我们**未存**（潜在扩展点） | 备注 |
|---|---|---|---|---|
| Reviews 顶层响应字段 | 4 | 2 | 2 | `success`/`query_summary` 仅用一次，没显式落库 |
| `query_summary`（6 字段） | 6 | 0 | 6 | 可进 `targets.extra_json` |
| 单评论 主体字段 | 21 | 6 | 15 | `weighted_vote_score` / `developer_response` 等都有价值 |
| `author.*` 子字段 | 11 | 1 | 10 | `num_games_owned` `num_reviews` `playtime_*` 极有价值 |
| App Details `data` 顶层 | 34 | 3 | 31 | `is_free` / `release_date` / `developers` 都该早入 |
| App Details 嵌套（价格等） | — | 0 | — | 收费游戏分析必备 |

> 💡 **结论**：当前仅采集了**约 11%** 的可用字段。扩展空间很大，但都按需引入，不一次堆。

---

## ⚠️ 6. 已知坑与封边条件

### 6.1 评论分页上限
- `num_per_page` 上限 **100**
- cursor 可能 URL 编码错误（Steam 文档明说"需要 URLEncoded"）
- 我们当前 `requests.get(..., params=...)` 会自动 URL encode，正确处理

### 6.2 速率限制
- Steam 文档没官方说明限速，但社区经验：**持续高频请求会触发 IP 临时封禁（5-15 分钟）**
- 缓解：每页间 `sleep(0.3-1.0)`，单次跑不超过 1000 条
- 严格生产环境建议：套代理池 + 重试 + 降级策略

### 6.3 Content-Type
- `appdetails` 默认返回 HTML，**必须**带 `Accept: application/json` 才能拿到 JSON（requests 库默认行为需手动设）
- `reviews` 用 `?json=1` 强制 JSON

### 6.4 缺失字段的兼容性
- 不同 app 返回的 `data` 字段差异巨大（独立游戏常常只有 4-8 个字段，3A 大作有完整 30+）
- 客户端必须把"字段缺失"当成正常情况，不要 raise
- 我们当前 SQLAlchemy 模型是"缺失就 NULL"策略，符合预期

### 6.5 时区
- Steam Unix 时间戳默认**UTC**
- 我们统一用 `_utcnow()`（UTC）+ 显式语义，避免时区错乱

### 6.6 隐私字段处理
- ⚠️ **永远不要把 `personaname` / `avatar` / `profile_url` 写进 DB 或导出 CSV**
- 即使合规要求"公开数据可用"，也要保守一点 —— Steam 用户对被爬取是很反感的
- 如果设计师强烈要求"显示昵称"，建议做**单向 hash**（只展示后 4 位 Steam64 ID）代替昵称

---

## 🚀 7. 实测原始响应文件（参考）

真实响应保存在项目本地：

| 文件 | 内容 |
|---|---|
| `.workbuddy/scratch/steam_appdetails_730.json` | CS2 的 appdetails 真实响应（17 KB） |
| `.workbuddy/scratch/steam_reviews_730.json` | CS2 中文区 reviews 实测（2 条，2.7 KB） |

> 这两个文件**不入 git**，可在本地跑 `python .workbuddy/scratch/inspect_appdetails.py` 或 `inspect_reviews.py` 重生成。

---

## ✅ 8. 一句话总结

> **Steam API 真实给我们 70+ 个可用字段，当前项目实际用了约 11%。扩展到 100% 都是按需引入，不一次堆，避免过早设计。**