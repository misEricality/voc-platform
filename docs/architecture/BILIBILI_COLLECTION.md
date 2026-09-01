# B 站数据采集设计（Bilibili Collection）

> **状态**：✅ 策略已定稿（2026-08-13，工程师确认）｜ **执行方**：独立开发窗口（本文件即规格说明书，自包含）
> **前置实测**：`scripts/dev/archive/diag/probe_bilibili.py` / `probe_bili_wbi.py`（2026-08-11 沙箱实测，接口可行性已验证；2026-09-01 归档）
> **关联文档**：[DATA_FIELDS.md](./DATA_FIELDS.md)（A/B/C/D 字段约定）· [STEAM_API_FIELDS.md](../STEAM_API_FIELDS.md)（Steam 对照）

---

## 一、业务背景与目标

VoC 平台已跑通 Steam 评论全链路（8302 条，2026-08-19：采集→打标→观点→向量→仪表盘）。B 站作为第二数据源，价值不在"第二个评论源"，而在三类增量资产：

1. **弹幕**：观看当下的即时情绪，带视频内时间戳（B 站独有，Steam 无对应物）；
2. **互动统计**：播放/三连/收藏 = 口碑强度代理指标；
3. **评论者画像**：B 站 reply 接口 author 字段自带 level/vip/sex/official，零成本启动"玩家画像"。

**采集定位**：非持续监控，而是「**发布满 7 天后的口碑稳态快照**」——与项目既有「7 天后回采」（`refresh_likes.py`）同一原则。

---

## 二、已验证接口清单（2026-08-11 实测 code=0）

| 用途 | 接口 | 关键参数 | 备注 |
|------|------|---------|------|
| 视频信息+互动统计 | `x/web-interface/view?bvid=` | bvid | 标题/分区 tid/tname/pubdate/UP主/简介 + `stat`（播放/弹幕/评论/收藏/投币/分享/点赞） |
| 视频标签 | `x/tag/archive/tags?bvid=` | bvid | 话题维度 |
| 评论列表 | `x/v2/reply`（type=1&oid=aid） | oid/pn/ps/sort | sort=2 按点赞；`member` 含 level/vip/sex/official |
| 弹幕 | `x/v1/dm/list.so?oid=cid` | cid | XML 格式，1200 条/页 |

**采集前置条件（必须，缺则 412）**：
1. 先调 `x/frontend/finger/spi` 拿 buvid3+buvid4 写入 cookie（.bilibili.com 域）；
2. 完整浏览器头：UA + Sec-Ch-Ua + Sec-Fetch-* + Origin + Referer；
3. `Accept-Encoding` 不带 `br`（requests 无 brotli 会乱码）。

**已知受限**：
- 超热门视频（如 22 万评论级）匿名访问被风控降级（replies 返回空）→ 需登录 cookie（SESSDATA）或限定普通热度视频；
- UP 主空间列表 `x/space/wbi/arc/search` 需 WBI 签名 + bili_ticket，沙箱云 IP 实测 -412 → **绕开**（见策略，不依赖此接口）。

---

## 三、数据模型映射（字段级规格，开发按此落库）

### 3.1 视频评论 → `comments` 表（复用现有表，platform=bilibili）

| comments 字段 | 来源（reply 接口） | 说明 |
|---|---|---|
| `platform` | 常量 | `"bilibili"` |
| `source_id` | `rpid` | 平台唯一 ID（唯一键 platform+source_id 复用） |
| `target_id` | 常量 | `f"bilibili:video:{aid}"` |
| `content` | `content` | 评论正文 |
| `author_id` | `member.mid` | 用户 mid |
| `rating` | — | B 站无评分，置 `NULL` |
| `language` | 常量 | `"zh-CN"` |
| `likes` | `like` | 点赞数（冷启动语义同 Steam：NULL=未回采，本项目固定快照模式，直接存实值） |
| `replies` | `rcount` | 楼中楼回复数 |
| `posted_at` | `ctime`（unix 秒） | 评论时间（落库为 **naive UTC**） |
| `extra_json` | 见 3.2 | 评论者画像 |

### 3.2 评论者画像 → `comments.extra_json`（玩家画像第一桶数据，零增量成本）

```json
{
  "uname": "昵称",
  "level": 6,
  "vip": {"status": 1, "type": 2},
  "sex": "男",
  "official": {"role": 1, "title": "认证信息"}
}
```

### 3.3 弹幕 → 新表 `danmaku`（DDL）

```sql
CREATE TABLE danmaku (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     VARCHAR(64) NOT NULL,        -- 'bilibili:video:{aid}'，对齐 comments.target_id
    cid          VARCHAR(32),                 -- 分 P 弹幕池 id
    content      TEXT NOT NULL,
    progress     INTEGER,                     -- 视频内时间点（秒）—— 情绪-内容时间轴
    mode         INTEGER,                     -- 弹幕类型（1=滚动 4=底部 5=顶部 7=高级）
    color        INTEGER,                     -- 弹幕颜色（可作情绪粗信号）
    user_hash    VARCHAR(32),                 -- 用户 hash（匿名，不落真实身份）
    posted_at    DATETIME,                    -- 弹幕发送时间（与 progress 双时间戳；naive UTC）
    fetched_at   DATETIME DEFAULT (datetime('now'))
);
CREATE INDEX ix_danmaku_video ON danmaku(video_id, progress);
```

### 3.4 视频元数据 → `targets` 扩展（type=video）

| 字段 | 来源 | 说明 |
|---|---|---|
| `target_id` | 常量 | `bilibili:video:{aid}` |
| `platform` | 常量 | `bilibili` |
| `external_id` | `bvid` | 视频标识 |
| `name` | `title` | 视频标题 |
| `type` | 常量 | `video` |
| `extra_json` | view 全量 | `aid/cid/tid/tname/pubdate/owner(mid,name)/desc/stat 快照/tags` |
| `extra_meta` | 派生 | 游戏名映射（若视频标题含游戏名） |

> `stat` 快照：播放 view / 弹幕 danmaku / 评论 reply / 收藏 favorite / 投币 coin / 分享 share / 点赞 like。三连率 `(like+coin+favorite)/view` = 口碑强度代理。

### 3.5 UP 主 → `user_profiles` 表（P2 预留，本次可不建）

| 字段 | 说明 |
|---|---|
| `platform` | `bilibili` |
| `user_id` | mid |
| `level/vip/sex/official` | 来自评论接口已可拿 |
| `fans/following/archive_count` | 需 `relation/stat`（P2 本机+cookie 再验证） |

---

## 四、采集策略（核心决策，已确认）

### 4.1 决策总表

| 场景 | 规则 |
|------|------|
| 视频筛选 | 仅采 `pubdate ∈ [now-7d, now]` 发布的视频 |
| 采集时机 | 视频发布**满 7 天后**执行（稳态快照，评论累积 95%+） |
| 评论数 ≤ T | **全量**（翻页到底） |
| 评论数 > T | **抽样**：绝对数量 K=1,000（点赞 top-600 + 最新 400） |
| 弹幕 | **永远抽样**：progress 时间轴均匀分片，单视频上限 ~3,000 条 |
| 重采 | 默认不重采；趋势功能（P8）启用后仅对 high-value 视频每周补采 |
| 评论数来源 | `view.stat.reply`（采视频信息时零成本获得，先判断再决定采集分支） |

### 4.2 阈值与抽样（关键设计）

- **阈值原则**：`T = reply 接口匿名可达翻页深度上限`，不是业务拍脑袋。全量只在接口给得全时才有意义（超出深度上限的"全量"是伪全量）。
- **初始值**：`T = 2,000`。**开发第一步必须实测校准**（拿一个 3-5 万评论视频翻页到空，确定真实深度，回填本表 4.2 节）。
- **抽样方法：绝对数量，非百分比**。理由：百分比使样本量随热度波动（10 万评论的 10% = 1 万，1 千的 10% = 100），下游统计置信度漂移；绝对数量保证每视频样本恒定、可比。
- **抽样内部分层**（修正高赞采样偏情绪化的偏差）：
  ```
  K = 1,000 = sort=2（按点赞）取 top-600
            + sort=0（按时间）取最新 400
  ```
- **联动规则**：若实测 T 变化，K = T/2（保持 K ≤ T 且成本可比）。例如 T=1,000 → K=500（高赞 300 + 最新 200）。

### 4.3 时间窗（与既有 7 天回采机制同构）

- 只采**过去 7 天发布**的视频（`pubdate ∈ [now-7d, now]`）；
- 采集动作在视频发布 **≥7 天后**执行 → 此时评论/弹幕已进入稳态，采 1 次即代表该视频口碑；
- 不采刚发布视频的原因：评论未稳定（需重采）+ 降低采集频率防风控；
- 弹幕持续新增但 progress 分布 7 天后已稳定，1 次采样足够。

### 4.4 弹幕抽样（双时间戳）

- 弹幕数据模型必须同时带：`progress`（视频内秒点，对齐内容段落）+ `posted_at`（发送时间，区分早期/后期弹幕）；
- 抽样：按 `progress` 时间轴均匀分片（如每 30 秒窗口取 N 条），单视频总量 ≤ 3,000；
- 弹幕**不进入 LLM 打标链路**（成本红线）：用词典匹配（复用 `normalize.match_l3` 思路）+ 时间窗聚合生成"情绪曲线"辅助信号。

### 4.5 防风控频率

- 请求间隔 ≥1s + 随机延迟（1-3s）；
- 错误退避：-412/-352 触发指数退避并暂停该视频；
- 采集上限：单次任务 ≤ 50 个视频，避免长跑触发 IP 风控。

---

## 五、实测校准结果（2026-08-13 · BV1UpwaeNESx 完成）

| # | 校准项 | 实测结论 | 影响 |
|---|--------|---------|------|
| 1 | reply 翻页深度上限 | 登录态下 pn=50 仍满 20 条，未触顶；**T=2,000 维持不变**（抽样分支已验证：4.6 万评论走 K=1000 抽样） | T 值保持 |
| 2 | 超热门视频 cookie 需求 | **必须 SESSDATA**：匿名只给第 1 页 3 条（pn≥2 全空）；登录后 20/20 满页 | .env 配 `BILIBILI_SESSDATA` |
| 3 | 弹幕分片阈值 | `list.so` 返回的是 B 站**防抖抽稀**子集（实测 1200 条，progress 覆盖全程均匀）→ **天然满足"弹幕永远抽样"，无需额外分片**；编码坑：必须 `content.decode('utf-8')` | 分片逻辑简化 |

**画像字段实测修正**（回填 3.2 节）：
- `level` → `member.level_info.current_level`（顶层 `level` 为 None）
- `official` → `member.official_verify`
- 实测样本：落花影I Lv6 / 制作人Soulframe Lv5 / 变戏法的-（sex=男）

**全链路验证**：`python -m src.pipeline --platform bilibili --target BV1UpwaeNESx --count 1000 --skip-analysis`
→ 采集 1006 条评论（抽样）+ 弹幕 1200 条 + 向量化 1000 条，全部落库 ✅，pytest 9/9 绿

---

## 六、合规约束

- 仅使用 B 站**公开接口**（web-interface / reply / dm），不绕过登录态获取私密数据；
- 不采集：用户私信、收藏列表、粉丝列表等隐私数据；
- 弹幕用户仅存 `user_hash`（接口本身匿名化），不落真实身份；
- 采集频率克制（4.5 节），遵守平台开发者协议与 robots 约定；
- 数据仅用于本项目学习研究目的，不对外商业化分发。

---

## 七、开发验收标准（执行窗口交付判定）

1. **采集器**：`src/collectors/bilibili.py` 基于 `probe_bilibili.py` 骨架，实现 `view → 决策分支 → reply 全量/抽样 → danmaku 分片` 全流程；
2. **少量数据跑通**：1 个真实视频，评论+弹幕+视频元数据全部落库，`pipeline --platform bilibili` 可调度；
3. **阈值分支**：构造 ≤T 与 >T 两个场景，验证全量/抽样分支正确；
4. **抽样可复现**：同一视频两次采集结果一致（固定排序 + 固定分片）；
5. **文档同步**：`DATA_FIELDS.md` 增补 B 站字段（含 danmaku 表）、`scripts/README.md` 登记新脚本；
6. **回归**：pytest 全绿（现有用例不破坏）；采集器单元测试待补（2026-08-15 评审已列为待办）。

---

## 八、成本基线（供预算参考）

| 项 | 单视频量级 | 说明 |
|---|---|---|
| 全量（≤2,000 条） | ~100 请求 × 1.5s ≈ 3 分钟 | 免费 |
| 抽样（1,000 条） | ~50 请求 | 免费 |
| 弹幕（≤3,000 条） | ~3 请求 × 分片 | 免费 |
| **LLM 打标**（仅评论抽样集） | 1,000 条 ≈ ¥5 | 走方案 4 批量打标；弹幕不打标 |
| 存储 | 万条级 ≈ 100MB 内 | SQLite 无压力，向量 2KB/条 |
