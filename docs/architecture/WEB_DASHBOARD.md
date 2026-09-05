# Web 实时看板（真前端）· 架构设计

> **用途**：把「静态原型 HTML（inline JSON 死数据）」升级为「真前端 + 实时 API 读库」，并新增 P8 时间序列页与「系统管理」页（采集任务管理）。本文档是实施蓝本 + 决策记录。
>
> **关联文档**：
> - 部署架构（Caddy + 127.0.0.1 bind + 安全红线）：[SELF_HOSTED_VPS_DEPLOYMENT.md](./SELF_HOSTED_VPS_DEPLOYMENT.md)
> - 自动化采集流水线（collect_tasks 的消费方）：[AUTOMATION_PIPELINE.md](./AUTOMATION_PIPELINE.md)
> - B 站队列状态机（paused 状态扩展对象）：[BILIBILI_AUTOMATION.md](./BILIBILI_AUTOMATION.md)
> - 前端视觉规范（tokens.css 单一来源）：[DESIGN_TOKENS.md](./DESIGN_TOKENS.md)
>
> **最后更新**：2026-09-01 · **状态**：🟢 实施中（阶段 1/5）

---

## 0. 一句话总览

> **FastAPI（`src/api/`）提供只读数据端点 + 管理员任务管理端点；`product/web/` 原生 ECharts SPA（无 Node 构建链）实时渲染 `data/voc.db`；Streamlit 并存不动。**

## 1. 决策记录（2026-09-01 工程师确认）

| # | 决策点 | 结论 | 理由 |
|---|---|---|---|
| 1 | 技术路线 | **FastAPI + 原生 ECharts SPA**（方案 A） | 零 Node 依赖，复用 DESIGN_TOKENS v1.0 与 v2 原型代码；符合「非必要不新增」 |
| 2 | Streamlit | **并存不动** | 降低风险；新前端独立演进 |
| 3 | 部署 | 公网（VPS + Caddy）+ **单管理员鉴权**（访客只读无鉴权） | 管理员仅做系统管理操作，无多用户体系 |
| 4 | 第一期范围 | 3 原型页实时化 + P8 时间序列页 + 系统管理页（界面从简） | 功能优先，界面后续精修 |
| 5 | Steam 任务存储 | **迁入 DB `collect_tasks` 表**；targets.yaml 降级为空表种子/回退 | VPS 形态 A 下 DB 单一权威源；web 写 git 跟踪文件会产生脏工作树 |
| 6 | 新增任务首次采集 | **触发，回采近 7 天** | 否则新任务只收每日增量，30 天才攒出像样数据 |
| 7 | 失败态 | **显示**。B 站表格 4 状态：待采集 / 已采集 / 已暂停 / 采集失败 | 静默失败是 8/27 已踩过的坑 |
| 8 | B 站任务存储 | **复用 `bilibili_queue` 表**（新增 `paused` 状态），不并入 collect_tasks | B 站有丰富的队列语义（due_date/fail_count 等），且已有 6 例测试；API 层做统一视图 |

## 2. 架构图

```
                    公网（Internet）
                         │ HTTPS（Caddy :443，TLS 终止）
                ┌────────▼─────────┐
                │  Caddy 反向代理   │
                └────────┬─────────┘
                         ▼  127.0.0.1:8000（仅本机）
        ┌────────────────────────────────────────┐
        │  FastAPI（src/api/，uvicorn systemd）   │
        │  ├─ 静态托管 product/web/（SPA 页面）    │
        │  ├─ 公开只读端点 /api/*（访客，无鉴权）  │
        │  └─ 管理端点 /api/admin/*（session 鉴权）│
        └────────────────┬───────────────────────┘
                         ▼  sqlite:///data/voc.db（WAL 模式，权限 600）
                ┌──────────────────┐
                │   data/voc.db    │  单一权威源（collect_tasks 在此）
                └────────▲─────────┘
                         │  每日 cron 写
                ┌────────┴──────────┐
                │ cron：daily_incremental_collect（读 collect_tasks）
                │ cron：bilibili run-due（读 bilibili_queue，跳过 paused）
                └───────────────────┘
```

**边界**：
- 前端静态文件由 FastAPI `StaticFiles` 托管（`/` → `product/web/`），Caddy 只需反代一个端口（8000）
- 公开端点**只读**，无任何写路径；写操作（任务 CRUD）全部在 `/api/admin/*` 后面
- SQLite 开 WAL 模式 + busy_timeout：前端服务随时读 × cron 每日写互不阻塞（根治 Streamlit 时代的文件锁坑）

## 3. 数据模型变更

### 3.1 新表 `collect_tasks`（Steam 采集任务）

```
id              INTEGER PK
platform        TEXT NOT NULL DEFAULT 'steam'   # 预留多平台
target_id       TEXT NOT NULL                   # appid（与 comments.target_id 的 id 部分对应）
name            TEXT                            # 游戏名（Steam appdetails 回填）
language        TEXT DEFAULT 'schinese'
count           INTEGER NULL                    # NULL = auto（时间窗耗尽模式）
source_url      TEXT                            # store 页 URL（表格显示用）
enabled         INTEGER NOT NULL DEFAULT 1      # 0 = 已暂停
created_at      DATETIME
last_collected_at DATETIME NULL                 # 采集侧可回写（预留）
UNIQUE(platform, target_id)
```

> 兼容性：init_db 自动建表（create_all）；`enabled` 为 NOT NULL 带默认值，但新表走 create_all 无需 ALTER。种子迁移见 §3.3。

### 3.2 `bilibili_queue` 扩展：新增 `paused` 状态

状态机变更：
```
pending → scheduled → fetching → fetched / failed
              ↑______________|            │
              （失败未达阈值回 scheduled） │
                                          ▼
                     新增：任意非终态 ──paused（人工暂停）
                     paused ──恢复──→ scheduled（pubdate 已识别）或 pending（未识别）
```

- runner `_select_due` 只查 `status='scheduled'`，**paused 天然被跳过**（无需改查询，补测试锁定行为）
- 恢复规则：`paused → scheduled`（若 pubdate 已识别）/ `paused → pending`（未识别，需重新识别）

### 3.3 种子迁移（targets.yaml → collect_tasks）

- `seed_collect_tasks_from_yaml()`：读 `config/monitoring/targets.yaml` 的 `targets` 段，逐条 upsert 进 `collect_tasks`（幂等，已存在跳过）
- 触发时机：`daily_incremental_collect.py` main() 初始化后调用（DB 优先 + 自动种子化）；`excluded_targets` 段**不迁移**（已归档目标保持排除）

### 3.4 目标加载逻辑变更（daily_incremental_collect.py）

```
load_targets(config_path)           # 原 yaml 加载，保留（回退路径）
load_targets_from_db(db_path)       # 新：读 collect_tasks enabled=1，格式对齐 yaml 条目 dict
main():
    targets = load_targets_from_db() or (seed + retry) or load_targets(yaml)
```

> GH Actions 过渡期影响（已接受）：网页端新增/修改的任务只存在于本地 DB，不会同步到 workflow 云端运行；VPS 形态 A（GH Actions 关停）下无此问题。

## 4. API 设计

### 4.1 公开只读端点（访客，无鉴权）

| 方法 | 路径 | 说明 | 数据来源（复用现有仓储方法） |
|---|---|---|---|
| GET | `/api/targets?platform=steam\|bilibili&monitored=` | 目标列表 + 聚合指标（`monitored=true` 仅返回 targets.yaml targets 段白名单，2026-09-03） | `list_targets(platform=None)` |
| GET | `/api/games/meta?targets=a,b,c` | **游戏元数据**（发行日期/Steam 全量评测数/评级描述/本地封面文件名；缺行或超 24h 自动刷新，失败返回 NULL 不阻塞） | `game_meta` 表 + Steam appdetails/appreviews 代理 |
| 静态 | `/covers/{appid}.jpg` | 游戏竖版封面（`data/covers/` 本地缓存，采集时从 Steam CDN 下载 library_600x900） | FastAPI StaticFiles |
| GET | `/api/overview?target=&start=&end=&grain=comment\|opinion` | 单目标 KPI + 情感分布（双颗粒度） | 聚合查询（新写薄封装） |
| GET | `/api/topics/tree` | **L1~L3 主题树**（`config/topics/gaming.yaml`，树状筛选器数据源） | yaml 直读（service 层轻量 loader） |
| GET | `/api/topics?target=&level=L1\|L2\|L3&grain=&sentiment=&start=&end=&full=` | 主题分布（原声/观点双颗粒度；`full=true` 按 yaml `primary` 顺序返回全部 L1 含零计数与「综合与元表达」） | opinions / comments.topic 聚合 |
| GET | `/api/comments?target=&page=&page_size=&sentiment=&topic=&q=&start=&end=&grain=&sort=time\|likes` | **原声分页列表**（`sort=time` posted_at desc（默认）；`sort=likes` 点赞降序→时间降序（B站原声列表）；附观点标签 + extra 解析游玩时长；`grain=comment` 时 topic 精确匹配 L1） | comments + comment_opinions join |
| GET | `/api/opinions?target=&page=&page_size=&sentiment=&topic=&start=&end=` | **观点分页列表**（观点粒度看板；每条附所属原声：原文/情感/主题/推荐/游玩时长；情感过滤在观点级） | comment_opinions join comments |
| GET | `/api/danmaku/{bvid}` | **弹幕时间轴**（30s 固定桶 + 每桶 10 条随机样本 ≤15 字，悬停浮层用） | `danmaku` 表聚合（`bucket_danmaku_rows`） |
| GET | `/api/bilibili/videos` | **B站视频看板数据源**（fetched 视频快照：封面/UP主/播放量/三连/时长/标签 + 采集量 + 性别分布 + 高光总结） | `bilibili_queue` 快照列 + `extra_json.profile.sex` 聚合 |
| GET | `/api/compare?targets=a,b,c` | 多目标对比聚合包 | `sentiment_ratio` / `opinion_matrix` / `negative_pain_points` |
| GET | `/api/trends?target=&days=30` 或 `&start=&end=` | **P8 时间序列**：按日聚合评论量 + 情感构成 + 推荐率（`recommend_rate` 无 rating 数据日为 `null`） | `posted_at` 日级 GROUP BY（新写） |

**2026-09-03 单游戏看板新增统一口径**（`src/api/service.py` docstring 有完整说明）：

1. **时间窗 `start`/`end`**（`YYYY-MM-DD` 闭区间）：overview / topics / comments / trends 均支持，统一作用在 `Comment.posted_at`（评论发布时间）；trends 的 `start/end` 优先，缺省回落 `days`（`pages/trends.js` 兼容）。
2. **颗粒度 `grain=comment|opinion`**（默认 opinion，保持旧消费方行为）：
   - 情感分布：comment → `comments.sentiment`（一条原声算一次）；opinion → `comment_opinions.sentiment`（多观点可重复计入，overview 附 `opinion_total`）
   - L1 主题：comment → `comments.topic`（= **主观点的 L1**；`comment_opinions` 无 `is_core` 列，主观点由打标时 core 判定物化进 `comments.topic`，NULL 排除）；opinion → `comment_opinions.full_path` 的 L1 段
   - `grain=comment` 时 `level` 必须 L1，否则 422
3. **全量零填充 `full=true`**：按 `gaming.yaml` 的 `primary` 顺序返回全部 10 条 L1（无数据补 0、含「综合与元表达」），供看板固定顺序条形图；该模式强制含元表达，默认（full 缺省）仍按总量 desc 只返回有数据项（compare 等既有消费方不回归）。

约定：全部返回 `{ok: true, data: ...}` 包装；错误 `{ok: false, error: {code, message}}`；日期字段 ISO 8601（naive UTC 语义与库内一致）。

### 4.2 管理员端点（session 鉴权）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/tasks/lookup?platform=steam\|bilibili&url_or_id=` | **弹窗「查找」**：即时返回目标标题与日期（Steam：name+发行日期；B站：title+投稿），不落库 | appdetails / view 接口代理 |
| POST | `/api/auth/login` | `{password}` → session cookie（密码哈希比对 `.env` `ADMIN_PASSWORD_HASH`） |
| POST | `/api/auth/logout` | 清 session |
| GET | `/api/admin/tasks?platform=steam\|bilibili` | 统一任务视图（steam → collect_tasks；bilibili → bilibili_queue） |
| POST | `/api/admin/tasks` | 新增任务（平台分派：见 §5 字段表）；可选 `backfill_days=7` 触发首次采集 |
| PATCH | `/api/admin/tasks/{platform}/{id}` | 编辑 / 暂停(enabled=0) / 恢复 |
| DELETE | `/api/admin/tasks/{platform}/{id}` | 删除（B 站 `fetched` 拒绝 → 409） |

鉴权实现：单管理员，`ADMIN_PASSWORD_HASH`（bcrypt/sha256 salt）存 `.env`；FastAPI middleware 校验 `/api/admin/*` 与 `/api/auth/*` 之外的豁免；session 用签名 cookie（itsdangerous/starlette SessionMiddleware）。首次部署需工程师手动生成 hash 写入 `.env`（提供 `scripts/ops/hash_admin_password.py`）。

### 4.3 新增 / 编辑字段表（平台分派）

**Steam**
| 字段 | 新增 | 编辑 | 说明 |
|---|---|---|---|
| url / appid | ✅ 必填 | 🔒 | 支持 store URL 或裸 appid；服务端解析 appid + 调 appdetails 回填 name |
| name | 自动回填，可改 | 可改 | |
| language | 默认 schinese | 可改 | |
| count | 默认 auto(null) | 可改 | |
| 暂停/恢复 | — | ✅ | enabled 开关 |

**BiliBili**
| 字段 | 新增 | 编辑 | 说明 |
|---|---|---|---|
| bv_id / URL | ✅ 必填 | 🔒 | 复用 `_normalize_bvid`；调 view 接口回填 title + pubdate，due = pubdate + 7d |
| note | 可选 | 可改 | |
| title / pubdate / due_date | 自动 | 🔒 | 系统计算；提供「重新识别」操作（重跑 view 接口） |
| 暂停/恢复 | — | ✅ | paused 状态；恢复按 §3.2 规则回 scheduled/pending |

**删除规则**：Steam 任意状态可删（历史数据保留在 comments 表，只停采）；B 站 `fetched` 拒绝删除（409），其余可删。

**状态映射（API 返回 `status_display`）**

| 显示 | Steam（collect_tasks） | BiliBili（bilibili_queue） |
|---|---|---|
| 采集中 | enabled=1 | fetching |
| 待采集 | —（Steam 无此态） | pending / scheduled |
| 已采集 | — | fetched |
| 已暂停 | enabled=0 | paused |
| 采集失败 | — | failed（附 fail_reason） |

## 5. 前端结构（product/web/）

```
product/web/
├── index.html          # SPA 壳（顶部导航 5 页 + 内容容器）
├── src/
│   ├── tokens.css      # ← 复制自 product/prototype/src/tokens.css（单一来源副本）
│   ├── app.js          # 路由(hash) + fetch 封装 + 分页/下钻状态
│   └── pages/*.js      # 每页一个渲染模块
├── dashboard.html?     # 或全部由 index.html hash 路由承载（实施时定，倾向单壳多 hash）
```

- 页面（2026-09-05 现役 5 页，前 3 页已按用户线框图重构）：单游戏看板（`#/dashboard`）/ 游戏对比看板（`#/compare`）/ B站视频看板（`#/bilibili`，封面+快照信息卡+性别/情感环形+L1 正负拆分联动筛选+可折叠原声列表+30s 弹幕时间轴+LLM 高光时刻）；「系统管理」一级导航 hover 下拉两个子模块 —— 采集任务（`#/admin`，登录后）/ 数据管理（`#/data`，原「时间序列」P8 页迁入，公开无需登录，`#/trends` 兼容跳转）
- 数据全部 `fetch('/api/...')`，不再 inline JSON；视觉复用 DESIGN_TOKENS v1.0
- ECharts 从本地 vendored 单文件引入（不走 CDN，内网可用）
- 系统管理页：平台 Tab 切换 + 任务表格（平台/游戏/URL/状态/操作）+ 新增/编辑弹窗（界面从简，功能完整）

## 6. 阶段拆解与验收

| 阶段 | 交付 | 验收 |
|---|---|---|
| 1 | 本文档 + 00-index 登记 | 文档可执行 |
| 2 | collect_tasks 表 + paused + WAL + 种子迁移 + daily/runner 适配 | pytest 全绿（新增存储用例） |
| 3 | src/api/ 全部端点 + 鉴权 | pytest API 用例 + uvicorn 本地 curl 实测 |
| 4 | product/web/ 五页 | 本地实走全部交互 |
| 5 | requirements（fastapi/uvicorn/itsdangerous）· VPS 文档更新（鉴权/WAL/双进程/8000 端口）· 登记四件套 · DEVELOPMENT_PLAN 关 P8 · AGENTS.md 版本记录 | §6 健康检查 8 条全过 |

## 7. 风险与已知取舍

| 风险/取舍 | 说明 | 对策 |
|---|---|---|
| GH 过渡期任务不同步 | 网页端改的任务只在本地 DB，workflow 云端看不到 | 已接受（§3.4）；VPS 上线后消失 |
| SQLite WAL 首次切换 | 已有 voc.db 切 WAL 会生成 -wal/-shm 文件 | 一次性自动完成；备份脚本已按文件拷贝，需注意 VACUUM 前 checkpoint |
| 首采回采 7 天的量级 | 新 Steam 任务首采 7 天 auto 模式可能耗时较长 | admin 触发为后台异步（后台线程）+ 前端轮询状态；失败不阻塞新增 |
| 公开端点信息暴露 | 评论原文/昵称公开可见 | 与现有合规声明一致（公开数据、伪匿名）；如需收紧再在 Caddy 层加 BasicAuth |
| B 站 view 接口风控 | 新增任务识别 pubdate 可能失败 | 失败入 pending + 提示稍后重试（「重新识别」操作兜底） |

## 📋 版本记录

| 更新时间 | 内容 | 原因 |
|---|---|---|
| 2026-09-03 | 单游戏看板重构：API 补时间窗（start/end）+ 双颗粒度（grain）+ 全量零填充（full）+ 推荐率日趋势 + 新端点 `/api/topics/tree`；前端 dashboard.js 重写（线框图 `product/prototype-design/线框图-单游戏看板.png`）；测试 18 → 28 例 | 看板页按新线框图升级为单游戏分析视角 |
| 2026-09-01 | 初版：8 项决策 + 架构图 + 数据模型 + API 设计 + 前端结构 + 阶段拆解 | Web 实时看板立项定稿 |
