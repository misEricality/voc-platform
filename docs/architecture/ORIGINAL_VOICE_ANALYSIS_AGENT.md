# 原声分析 Agent · 架构设计

> **用途**：在 Web 看板中嵌入一个"原声分析 Agent"，让平台读者通过自然语言交互，驱使 LLM 按需求和目标查询 `voc.db`、调用分析工具、输出结论，并把对话历史跨页面收纳到全局记录中心。
>
> **关联文档**：
> - Web 实时看板整体：[WEB_DASHBOARD.md](./WEB_DASHBOARD.md)（FastAPI / SPA / 5 页路由 / token + 鉴权）
> - 设计 token：[DESIGN_TOKENS.md](./DESIGN_TOKENS.md)（抽屉 UI 复用）
> - 数据字段：[DATA_FIELDS.md](./DATA_FIELDS.md)（agent tool 调用的返回字段定义）
> - 自动化采集：[AUTOMATION_PIPELINE.md](./AUTOMATION_PIPELINE.md)（agent 查询的是每日增量后的 DB）
> - 静态快照部署：[STATIC_SNAPSHOT_DEPLOYMENT.md](./STATIC_SNAPSHOT_DEPLOYMENT.md)（静态版不含 Agent，需在 FastAPI 模式才有）
>
> **最后更新**：2026-09-09 · **状态**：🟡 方案定稿，待落地

---

## 0. 一句话总览

> **FastAPI 暴露 `/api/agent/*` 端点（chat SSE + sessions CRUD + search_docs + export），复用现有 `src/api/service.py` 数据层 + `src/storage/db.py` 仓储，加 2 张新表（`agent_sessions` / `agent_messages`，含 `anon_user_id` 匿名标识）+ 1 个轻量 skill 系统（YAML + Python tool）；前端全局 `agent-drawer.js` 抽屉以 sparkle icon 浮动球形态挂在右下，所有页面共享入口 + 每页暴露 `window.__pageAgentContext` 注入上下文；会话默认保留 30 天，可一键导出 Markdown。**

> ⚠️ **2026-09-10 现役状态修正（UX 反转，覆盖下述决策）**
> 1. **`#/agent` 一级页面已下线归档**（`pages/agent.js/.css` → `pages/archive/`）——决策 #9/#19/#22 随之反转：顶栏 nav 无 AI 项，**默认首页恢复 `compare`**，历史对话/导出/删除等功能迁入抽屉**大窗左栏**。
> 2. **抽屉改两段式**：小窗 420×75vh ⇄ 大窗 ~900×800（头部左上箭头扩大 / 右下箭头缩回；大窗含历史栏）；展开渐现 + 关闭渐灭。
> 3. **「引用当前查询」重设计**（取代 #8 的 quick_query 方案——原实现因 `typeof` 判断 bug 从未显示）：头部「📥 引用当前查询 / 🗑 清除引用」双按钮，仅 dashboard/bilibili/compare 生效（各页注入 `build_summary` 聚合摘要器），引用存抽屉状态跨页保留、仅清除消失；摘要经 `ChatBody.context` 注入 system prompt（≤2000 字符，**不落库**）。
> 4. **Markdown 渲染**：vendor `marked.min.js` + `purify.min.js`；assistant 消息 done 后整体渲染（流式期间纯文本）。
> 5. **usage 管道已就位但受上游限制**：`stream_options.include_usage` 在 DeepSeek **tools+流式**模式下不返回 usage（服务端行为，非流式正常）——管道齐全、优雅降级，上游支持后自动生效。
> 6. **模糊指代约束**：system prompt 第 5 条——有页面上下文时「这游戏」默认指该 target；无上下文且指意不明先反问，禁止猜测。

---

## 1. 决策记录

| # | 决策点 | 结论 | 理由 |
|---|---|---|---|
| 1 | 集成路径 | **方案 B（自写 FastAPI chat + Lynx 原生抽屉 UI）** | DSH iframe 评估：DSH UI 是"侧栏+主区"壳，抽屉式嵌入需 fork client；DSH 的 subagent/workflow/skill 生态强大但 P0/P1/P2 场景用不到，借壳成本 > 自写成本 |
| 2 | LLM 协议 | **OpenAI 兼容 SSE chat completion + function calling** | DeepSeek-V4-Flash（2026-09-08 起主标注器）原生支持；零依赖 |
| 3 | 默认 LLM | **DeepSeek-V4-Flash**（与离线打标同源） | 凭据 `DEEPSEEK_API_KEY` 已就位；共享 prompt 规范；谷时 0.05 元/M 缓存命中 + 输出 4.5 元/M，便宜 |
| 4 | tool 数量 | **首版 4 个**：`query_overview` / `query_topics` / `query_comments` / `search_docs` | 覆盖 P0（数据查询/统计） + P2（项目文档 FAQ），够 demo |
| 5 | skill 系统形态 | **YAML prompt 模板 + Python tool 包装**（轻量版） | agent 调 tool 时自动匹配 skill prompt；不引入 DSH skill 协议 |
| 6 | 抽屉 UI 位置 | **悬浮球 + 向左上铺开**（右下 inset 32px / 80px，大圆角 + 阴影，scale+opacity 动画） | 工程师 2026-09-09 反馈：原"贴右下角"太"贴边"，要"漂浮在界面上"的视觉感；不接触右、下边缘 |
| 7 | 跨页上下文 | **`window.__pageAgentContext` 全局变量**（每页自己 set） | 简单直接，零依赖 |
| 8 | 一键引用 | **抽屉顶"📋 引用当前查询"按钮**：调一次 `__pageAgentContext.quick_query` URL，把返回数据塞进对话 | 用户说"一键直接获取当前查询出来的评论明细做总结" |
| 9 | AI 记录中心 | ~~提升为一级页面 `#/agent`~~ **2026-09-10 反转：一级页下线归档，历史栏迁入抽屉大窗** | 工程师 2026-09-10：一级页与其他页面交互冗余，入口统一悬浮球 |
| 10 | 鉴权 | **公开端点 + 速率限制**（与现有 `/api/*` 公共只读一致） | 不增加复杂度；未来加鉴权在中间件层加一行 |
| 11 | 渲染后端 | **Lynx 原生 SPA**（同 WEB_DASHBOARD 路线） | 抽屉是 `position:fixed` 元素，与主页内容共存；复用 tokens.css |
| 12 | 流式协议 | **Server-Sent Events（SSE）** | 比 WebSocket 简单；浏览器原生 `EventSource`；FastAPI `StreamingResponse` 一行起 |
| 13 | agent 流式渲染粒度 | **token 级流式 + tool_call 单独一轮显示**（用户能看到"正在查 DB..."） | 体验关键——避免静默 5-10 秒 |
| 14 | skill 编辑入口 | **仅服务端 YAML，工程师维护**（暂不做 UI 编辑） | 用户原话"主要给 agent 调用"；用户级 skill 编辑暂不实现 |
| 15 | Agent 公开 vs 限制 | **公开 + 速率限制**（IP 60 req/min，与 `/api/auth/login` 同框架） | 防滥用；不挡正常使用 |
| 16 | 匿名用户标识 | **前端生成 anon_user_id UUID 存 localStorage** + 后端 `agent_sessions.anon_user_id` 列 + API 按此隔离 | 工程师 2026-09-09 确认。无登录场景下"我的对话"归属最小必要标识；UUID 非 PII，未来可平滑升级为登录态 |
| 17 | 默认保留期 | **30 天滚动裁剪**（凌晨 03:30 cron 级联删 sessions + messages） | 工程师 2026-09-09 决定。SQLite 单库到 10GB 仍流畅，30 天对个人/小团队足够；匿名数据不无限期保留更合规 |
| 18 | 导出能力 | **`/api/agent/export` 端点 + AI 页面"📥 导出我的对话"按钮**；格式 Markdown（含对话时间 + 文本），严格按 `anon_user_id` 隔离，无 admin 通配 | 工程师 2026-09-09 要求"用户和我能下载，含时间和文本，每个用户只看/导自己的" |
| 19 | 一级导航顺序 | ~~AI 左起第一 + 默认首页 agent~~ **2026-09-10 反转：nav 无 AI 项（入口=悬浮球），默认首页恢复 compare** | 工程师 2026-09-10：一级页下线后导航回归 |
| 20 | AI 入口 icon | **sparkle 风格 SVG**（4 颗星 + 十字，单色 `currentColor`，24x24，候选 B） | 工程师 2026-09-09 选 B，机器人风格"太丑" |
| 21 | 冷启动示例 | **从"展示"白名单中固定选 3 个**（与 nav 出现的目标一致），不动态抽 | 工程师 2026-09-09 决定。白名单稳定即示例稳定，无需每次刷新换；白名单由 `monitored.yaml ∪ collect_tasks`（visible≠false）派生 |
| 22 | 老用户迁移提示 | **不加** | 工程师 2026-09-09 直接打开 `/` 默认跳 AI ~~~~ 2026-09-10 默认页已恢复 compare，此决策随之失效 |

---

## 2. 架构图

```
                     公网（Internet）
                          │ HTTPS（Caddy :443，TLS 终止）
                 ┌────────▼─────────┐
                 │  Caddy 反向代理   │
                 └────────┬─────────┘
                          ▼  127.0.0.1:8000（仅本机）
         ┌────────────────────────────────────────────────────┐
         │  FastAPI（src/api/，uvicorn systemd）                │
         │  ├─ 静态托管 product/web/（SPA 页面 + 抽屉组件）     │
         │  ├─ 公开只读端点 /api/*（已有 14 个）                │
         │  ├─ 管理端点 /api/admin/*（session 鉴权）            │
         │  └─ 新增 Agent 端点 /api/agent/*（公开 + 速率限制）   │
         │       ├─ POST /api/agent/chat          （SSE 流式）  │
         │       ├─ GET  /api/agent/sessions     （历史列表）  │
         │       └─ POST /api/agent/search       （文档搜索）  │
         └────────────────┬───────────────────────────────────┘
                          ▼  sqlite:///data/voc.db（WAL 模式）
         ┌────────────────────────────────────────────────────┐
         │  data/voc.db                                         │
         │  ├─ comments / comment_opinions / danmaku / ...（已有）│
         │  ├─ agent_sessions       （新）                       │
         │  └─ agent_messages       （新）                       │
         └────────────────────────────────────────────────────┘
                          ▲
                          │ 每用户对话
                          │
         ┌────────────────┴───────────────────────────────────┐
         │  Lynx Web SPA（product/web/）                       │
         │  ├─ pages/dashboard.js / compare.js / ...           │
         │  │     └─ 设置 window.__pageAgentContext            │
         │  ├─ pages/agent-history.js（新页 · 全局记录）        │
         │  └─ agent-drawer.js（新 · 全局抽屉）                 │
         │       ├─ 浮动按钮（右下角，跨页可见）                 │
         │       ├─ 抽屉面板（右侧滑出，35% 屏宽）              │
         │       ├─ "📋 引用当前查询"按钮 → 调 quick_query     │
         │       └─ 流式渲染（token + tool_call 状态）          │
         └─────────────────────────────────────────────────────┘
                          │
                          ▼
         ┌────────────────────────────────────────────────────┐
         │  DeepSeek-V4-Flash（外部 LLM API）                   │
         │  └─ OpenAI 兼容协议 + SSE + function calling          │
         └─────────────────────────────────────────────────────┘
```

**边界**：
- Agent 端点公开可读，与 `/api/*` 一致；**不读个人隐私数据**（只读已对外公开的评论/观点）
- SSE 流式响应**不缓存**（`Cache-Control: no-store`）
- Agent 调用 tool 全部走 `src/api/service.py` 现有数据层（**零重复 SQL**）
- 抽屉组件全局只 1 个 DOM 实例，不重复创建

---

## 3. 数据模型变更

### 3.1 新表 `agent_sessions`（对话会话）

```
id              TEXT PRIMARY KEY       -- uuid（生成时直接给客户端，便于断线重连）
page            TEXT NOT NULL          -- dashboard / compare / bilibili / data / admin / agent / global
page_context    TEXT                   -- JSON：当时的 window.__pageAgentContext 快照
title           TEXT                   -- 自动生成（首轮 user message 前 30 字）
model           TEXT NOT NULL          -- "deepseek-v4-flash"
anon_user_id    TEXT                   -- 匿名用户 UUID（前端 localStorage 生成）；列表/导出严格按此过滤
created_at      DATETIME
updated_at      DATETIME
INDEX(ix_agent_session_page, page)
INDEX(ix_agent_session_updated, updated_at DESC)
INDEX(ix_agent_session_anon, anon_user_id)
```

> `id` 用 uuid 字符串而非 INTEGER 自增：方便前端立即持有，断线重连无需 round-trip。
> `page_context` 存 JSON 字符串（不是真 JSON 列）：SQLite 无原生 JSON 类型，TEXT + Python `json.loads` 即可。
> `anon_user_id` 是 2026-09-09 新增字段（决策 #16）：前端首次访问生成 UUID 写 localStorage，后续所有会话自动带上；列表默认按此过滤，导出严格按此隔离。

### 3.2 新表 `agent_messages`（对话消息）

```
id              INTEGER PRIMARY KEY AUTOINCREMENT
session_id      TEXT NOT NULL          -- 关联 agent_sessions.id（ON DELETE CASCADE）
role            TEXT NOT NULL          -- user / assistant / tool
content         TEXT                   -- 文本内容（assistant 流式累积到 final）
tool_calls      TEXT                   -- JSON：assistant 调的工具（[{id, name, args}]）
tool_call_id    TEXT                   -- tool 响应对应的 assistant 调用的 id
tool_name       TEXT                   -- 冗余存一份方便查询（避免每次 parse tool_calls）
created_at      DATETIME
INDEX(ix_agent_msg_session, session_id, created_at)
```

> `content` 存"已完成"的文本（assistant 流式 token 实时推 SSE，不进 DB；final 落库）。
> `tool_calls` 用 JSON 数组——一个 assistant message 可能并行调多个 tool。
> FK `session_id → agent_sessions.id ON DELETE CASCADE` 让 30 天裁剪脚本一条 DELETE 级联清两张表。

### 3.3 保留策略（2026-09-09 新增，决策 #17/#18）

- **默认保留 30 天**：`scripts/ops/prune_agent_history.py` 每天 03:30 跑（错峰于 03:00 daily 检查）
- **`AGENT_RETENTION_DAYS`** env 可覆盖（首版默认 30；工程师可手动调高）
- **导出不受影响**：用户随时可导出 Markdown；导出文件不含用户身份字段，只含 `anon_user_id`（用户自己看得到，不外发）
- **`scripts/ops/prune_agent_history.py` 伪代码**：

```python
RETENTION_DAYS = int(os.environ.get('AGENT_RETENTION_DAYS', 30))
def prune():
    cutoff = (datetime.utcnow() - timedelta(days=RETENTION_DAYS)).isoformat()
    with db() as conn:
        cur = conn.execute('DELETE FROM agent_sessions WHERE created_at < ?', (cutoff,))
        log.info(f'pruned {cur.rowcount} sessions older than {RETENTION_DAYS}d')
        # ON DELETE CASCADE 自动删 messages
```

### 3.4 迁移策略

`init_db()` 自动建表（沿用 2026-08-21 P10 init_db 自动演进模式），零手工迁移。**首次启动时若表已存在则不重建**，幂等。

### 3.5 不放哪些数据

- **不存 user 提问的 PII**（anon_user_id 是无意义 UUID，不算 PII）
- **不存 SSE 中间 token**（只存 final，减小 DB 体积；token 实时丢弃）
- **不存 tool 中间结果**（tool 返回一次性塞给 LLM，下一轮不再需要；除非用户主动引用）
- **不存跨用户查询**（导出 API 不接受 `?all=true` 通配，admin 想看全量走 `/api/admin/*`）

---

## 4. API 设计

### 4.1 公开端点（与现有 `/api/*` 一致鉴权级别）

| 方法 | 路径 | 说明 | 响应 |
|---|---|---|---|
| POST | `/api/agent/chat` | **SSE 流式对话**；body `{session_id?, message, page_context?, page}` | `text/event-stream`：event 序列 `meta`（session_id）→ `token`（LLM delta）→ `tool_call`（tool 开始）→ `tool_result`（tool 返回）→ `done`（结束）；可中途 client_disconnect 终止 |
| GET | `/api/agent/sessions?page=&limit=&offset=` | **历史会话列表** | `{items: [{id, title, page, created_at, updated_at, message_count}], total}` |
| GET | `/api/agent/sessions/{id}` | **单会话完整消息** | `{session: {...}, messages: [...]}`（按 created_at 升序） |
| DELETE | `/api/agent/sessions/{id}` | **删除会话** | `{ok: true}`（级联删 messages） |
| POST | `/api/agent/search?q=&top_k=5` | **P2 FAQ tool 公开入口**（前端调试用） | `{items: [{path, snippet, score}]}`（score 是简单 keyword 命中数） |

**SSE 事件 schema**（与 OpenAI 风格兼容，前端易处理）：

```
event: meta
data: {"session_id": "abc-123", "model": "deepseek-v4-flash"}

event: token
data: {"delta": "玩家"}

event: token
data: {"delta": "最常"}

event: tool_call
data: {"id": "call_1", "name": "query_topics", "args": {"target": "steam:2358720", "sentiment": "negative"}}

event: tool_result
data: {"call_id": "call_1", "name": "query_topics", "result": {...}}

event: done
data: {"finish_reason": "stop", "usage": {"prompt_tokens": 1234, "completion_tokens": 567}}
```

**速率限制**（与现有 `/api/auth/login` 同框架，`src/api/auth.py` 已有）：
- 60 req/min/IP（首版宽松，未来按需调严）
- 超额返回 429 + Retry-After

### 4.2 Tool schema（function calling 定义）

tool 定义放在 `config/agent/tools.yaml`，agent 启动时加载到 system prompt + function calling schema：

```yaml
# config/agent/tools.yaml
- name: query_overview
  description: |
    查询某游戏/视频的评论量、推荐率、情感分布、KPI。
    参数：target_id（必填）、start/end（可选，YYYY-MM-DD）、grain（comment|opinion）。
    返回：评论总数、推荐率（%）、情感分布（{positive, neutral, negative}）、首末评论日期。
  impl: src.agent.tools:query_overview  # Python 函数路径

- name: query_topics
  description: |
    查询某游戏/视频的主题分布（按 L1/L2/L3）。
    参数：target_id（必填）、level（L1/L2/L3，默认 L1）、grain、sentiment、start/end、full（true=按 yaml 顺序零填充）。
    返回：[{topic, total}] 列表。
  impl: src.agent.tools:query_topics

- name: query_comments
  description: |
    查询某游戏/视频的评论明细（原声或观点）。
    参数：target_id、page（默认 1）、page_size（默认 20，最大 50）、sentiment、topic、q（关键词）、start/end、grain、sort（time|likes）。
    返回：{items: [{id, content, sentiment, topic, posted_at, likes, replies, playtime, opinions}], total}。
  impl: src.agent.tools:query_comments

- name: search_docs
  description: |
    搜索项目文档/代码，回答项目使用、采集流程、分析方法等问题。
    参数：query（关键词）、top_k（默认 3）。
    返回：[{path, snippet, score}]（覆盖 docs/00-index.md、AGENTS.md、README.md、docs/architecture/*、docs/guides/*、docs/plan/*）。
  impl: src.agent.tools:search_docs
```

**为什么 tool 放在 yaml 而非代码里**：
- 不污染代码（按 §3 红线："prompt / 业务配置 / API Key 写死在代码里，统一走 config/"）
- agent tool 描述对 LLM 极其敏感（描述模糊 → LLM 不知道何时调），yaml 可让工程师迭代不重启

### 4.3 Skill 系统（轻量版）

`config/agent/skills/*.yaml`，每个文件一个 skill：

```yaml
# config/agent/skills/negative_pain_points.yaml
name: negative-pain-points
description: 提取某游戏近 N 天负向评论的痛点 top-5
required_tools: [query_topics, query_comments]
output_format: |
  请按以下格式输出：
  1. **<痛点主题>**（<L2/L3>，占比 X%）：<典型引用>
  ...
prompt_template: |
  请基于以下数据找出玩家最常抱怨的 5 个具体问题：

  ## 话题分布（按 L2 排序，负向评论）
  {{query_topics.result}}

  ## 负向评论样本（最多 30 条）
  {{query_comments.result}}

  {{output_format}}
```

**skill 匹配逻辑**（前端不可见，agent 后端逻辑）：
1. agent 调用 tool 后（如 `query_topics` 返回负向话题分布）
2. 后端扫描 `config/agent/skills/*.yaml`，匹配 `required_tools` 与已调 tool 集合有交集的 skill
3. 匹配命中 → 把 skill 的 `prompt_template` 渲染（用 tool 返回数据替换 `{{...}}`）→ 作为附加 system message 注入下一轮 LLM
4. LLM 看到 skill prompt → 按 `output_format` 格式化输出

**为什么这个机制对 P1 关键**：
- 不引入 DSH skill 协议
- 工程师**只改 yaml**就能新增/修改分析流程
- agent 自动按"用到了哪些 tool"匹配 skill，无需 LLM 决策（确定性）

### 4.4 Prompt 模板

**主 system prompt**（`config/agent/system_prompt.txt`）：

```
你是「灵听 Lynx · 原声分析助手」，专门帮助读者分析 Steam / B 站玩家评论数据。

你可以调用以下工具：
{tool_schemas}

输出风格：
- 直接给答案，不要先说"我来帮你查..."
- 引用数据时标注来源（如"近 30 天底特律负向评论中..."）
- 数字用阿拉伯数字，比例用 %
- 玩家引用用「」包裹，最多引 5 条

约束：
- 不知道就说不知道，不要编造数据
- 不要重复工具返回的原始 JSON，加工成可读结论
- 用户问"为什么"时给原因；问"多少"时给数字
```

**首条 user message**（自动拼装，每轮都重拼）：

```
[当前页面上下文]
page: dashboard
target_id: steam:2358720
game_name: 底特律：变人
range: 近30天
grain: comment
sentiment_filter: negative
topic_filter: 玩法与内容/玩法机制

[用户消息]
{user_input}
```

---

## 5. 前端结构（product/web/）

### 5.1 新文件清单（按 §1 归属决策表）

| 文件 | 归属 | 职责 |
|---|---|---|
| `product/web/src/agent-drawer.js` | `product/web/src/` | 全局悬浮球 + 抽屉组件（跨页右下浮动） |
| `product/web/src/agent-drawer.css` | `product/web/src/` | 抽屉样式（独立文件方便 review） |
| `product/web/src/pages/agent.js` | `product/web/src/pages/` | **一级页面 `#/agent` 主入口**（带左侧历史栏 + 主对话窗口 + 冷启动示例） |
| `product/web/src/utils/anon-id.js` | `product/web/src/utils/` | anon_user_id UUID 生成 + localStorage 读写 |
| `config/agent/system_prompt.txt` | `config/agent/` | 主 system prompt |
| `config/agent/tools.yaml` | `config/agent/` | 4 个 tool schema（description + Python impl 路径） |
| `config/agent/skills/*.yaml` | `config/agent/skills/` | skill 模板（按决策 #5 形态） |
| `src/agent/` | `src/agent/` | Python 实现层：tools.py + skills.py + chat.py（SSE 流）+ retention.py |
| `src/api/routers.py`（改） | `src/api/` | 新增 `/api/agent/*` 端点 |
| `scripts/ops/prune_agent_history.py` | `scripts/ops/` | 30 天裁剪脚本（注册 `VOC-Local-Agent-Prune` 计划任务） |
| `tests/test_agent_*.py` | `tests/` | agent 单元/集成测试 |
| `product/web/index.html`（改） | — | nav 加 AI 一级项（sparkle icon）+ 引入 agent-drawer.js + agent.js |

### 5.2 抽屉 UI 草图（2026-09-09 改为悬浮球 + 向左上铺开）

```
┌──────────────────────────────────────────────────┐
│  Lynx  ✨ AI  Steam游戏看板▾  B站视频看板  系统管理▾ │  ← 顶栏新增 AI 一级项
├──────────────────────────────────────────────────┤
│  <页面正常内容>                                     │
│                                                   │
│                                                   │
│             ┌─────────────────────────────────┐  │
│             │  🤖 原声分析助手          × 关闭 │  │
│             │  ─────────────────────────────  │  │
│             │  当前页面：Steam单游戏            │  │
│             │  上下文：底特律 · 近30天 · 负向   │  │
│             │  [📋 引用当前查询]                │  │
│             │  ─────────────────────────────  │  │
│             │                                  │  │
│             │  消息列表（流式渲染）              │  │
│             │                                  │  │
│             │                                  │  │
│             │  ─────────────────────────────  │  │
│             │  [输入框           ]  [发送 ➤]   │  │
│             └─────────────────────────────────┘  │
│                                  ●  ← sparkle 浮动球│
│                                  （不贴边，hover 浮起│
│                                   scale + shadow 加深│
│                                   ）                │
└──────────────────────────────────────────────────┘
       ↑ 距右边 32px    ↑ 距下边 32px
          ↑ 距下边 80px（让出浮动球位置）
```

**关键视觉参数**（2026-09-09 决策 #6）：
- 浮动球位置：`right: 32px; bottom: 32px`（不贴边）
- 抽屉展开位置：`right: 32px; bottom: 80px; width: 420px; height: 75vh`
- 圆角：`border-radius: 16px`
- 阴影：`box-shadow: 0 12px 48px rgba(0,0,0,0.35)`（"漂浮"感关键）
- 展开动画：`scale(0.85) + opacity(0)` → `scale(1) + opacity(1)`，`transform-origin: bottom right`，200ms ease-out
- 浮动球 hover：`translateY(-2px)` + 阴影加深

### 5.3 `#/agent` 一级页面布局（2026-09-09 决策 #9 取代原 `#/agent-history`）

```
┌──────────────────────────────────────────────────────────────────┐
│  ✨ AI 原声分析助手                              [📥 导出] [⚙️]   │
├───────────────────────┬──────────────────────────────────────────┤
│  对话历史              │ 当前对话                                   │
│  ──────────────       │ ┌──────────────────────────────────────┐ │
│  🔍 搜索历史           │ │ 当前页面：AI 主页（无上下文）           │ │
│  [+ 新对话]            │ │                                      │ │
│  ──────────────       │ │ 消息列表（流式渲染）                    │ │
│  ▸ 底特律近 30 天      │ │                                      │ │
│    负向痛点 top5       │ │                                      │ │
│    📄 dashboard        │ │                                      │ │
│    9/8 15:23 · 4 条    │ │                                      │ │
│  ──────────────       │ │                                      │ │
│  ▸ 黑神话差评率对比     │ │                                      │ │
│    📄 compare          │ │                                      │ │
│    9/8 14:10 · 6 条    │ │                                      │ │
│  ──────────────       │ │ ─────────────────────────────────── │ │
│  ▸ ...                 │ │ [输入框              ] [发送 ➤]      │ │
│                       │ └──────────────────────────────────────┘ │
│  ──────────────       │                                          │
│  💡 对话默认保留 30 天 │                                          │
│                       │                                          │
└───────────────────────┴──────────────────────────────────────────┘
```

**冷启动界面**（无历史会话时主对话区显示）：

```
┌──────────────────────────────────────────────┐
│  你可以试试：                                 │
│  ┌────────────────────────────────────────┐  │
│  │ 底特律近 30 天负向评论主要痛点是什么？   →│  │
│  └────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────┐  │
│  │ 艾尔登法环和黑神话悟空推荐率差多少？     →│  │
│  └────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────┐  │
│  │ 这个视频弹幕里夸画面的有多少条？         →│  │
│  └────────────────────────────────────────┘  │
│  💡 例：可问"对比6款游戏差评率" / 切换页面后  │
│     Agent 自动带入上下文 / 支持一键引用当前页 │
│     面筛选结果                                │
└──────────────────────────────────────────────┘
```

**冷启动示例问题来源**（2026-09-09 决策 #21）：从"展示"白名单（`monitored.yaml ∪ collect_tasks WHERE visible != false`）的前 3 个 Steam 游戏 + 1 个 B站视频派生，**固定而非动态抽**：

```js
// agent.js 启动时执行一次
async function buildColdStartExamples() {
  const meta = await API.get('/api/games/meta');   // 6 款（已是白名单）
  const bili = await API.get('/api/bilibili/videos');
  const okGames = meta.games.filter(g => (g.total || 0) > 0).slice(0, 2);
  const okVideos = bili.videos.filter(v => (v.danmaku_total || 0) > 0);
  const v = okVideos[0];
  return [
    `${okGames[0].name} 近 30 天负向评论主要痛点是什么？`,
    `${okGames[1].name} 和 ${okGames[0].name} 推荐率相差多少？`,
    `${v?.title || '最近视频'} 弹幕里夸画面的有多少条？`,
  ].filter(Boolean);
}
```

> 白名单稳定即示例稳定；新游戏加入白名单（`collect_tasks` 新建任务且默认 visible）→ 重新生成首屏。

### 5.4 跨页上下文协议

**每个 page 在自己 `renderXxx()` 末尾设置**：

```js
// pages/dashboard.js
Routes.dashboard = async function (app) {
  // ... 现有逻辑 ...
  window.__pageAgentContext = {
    page: 'dashboard',
    target_id: state.target,
    target_name: ordered.find(t => t.target_id === state.target)?.name,
    range: state.range,
    grain: state.grain,
    sentiment_filter: state.senti,
    topic_filter: state.topic,
    quick_query: `/api/comments?${qs({ grain: state.grain, sentiment: state.senti, topic: state.topic, page_size: 50 })}`,
  };
};
```

**`#/agent` 主页设置特殊上下文**（无目标）：

```js
// pages/agent.js
window.__pageAgentContext = { page: 'agent' };  // 无 quick_query
```

**抽屉启动时读取**：

```js
// agent-drawer.js
function getPageContext() {
  return window.__pageAgentContext || { page: 'global' };
}
```

**"引用当前查询"按钮**：

```js
async function citeCurrentQuery() {
  const ctx = getPageContext();
  if (!ctx.quick_query) {
    toast('当前页面没有可引用的查询（试试去对比/单游戏/B 站页面）', true);
    return;
  }
  const resp = await API.get(ctx.quick_query);
  appendUserMessage({
    text: `已引用当前查询（${ctx.page} · ${ctx.target_name || ctx.target_id} · ${resp.items.length} 条）`,
    attachment: { type: 'page_context', data: resp },
  });
}
```

### 5.5 匿名用户标识（决策 #16）

```js
// utils/anon-id.js
export function getAnonUserId() {
  let id = localStorage.getItem('lynx_anon_id');
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem('lynx_anon_id', id);
  }
  return id;
}

// api.js 拦截器
api.interceptors.request.use(cfg => {
  cfg.headers['X-Anon-User-Id'] = getAnonUserId();
  return cfg;
});
```

**隐私说明**：抽屉顶部 + AI 页面底部加一行小灰字"匿名 UUID 仅用于保存您的对话历史，不含个人信息"。

### 5.6 导出按钮（决策 #18）

AI 页面顶部 + 抽屉设置弹窗都有"📥 导出我的对话"按钮：

```js
async function exportMyHistory() {
  const blob = await fetch('/api/agent/export', {
    headers: { 'X-Anon-User-Id': getAnonUserId() },
  }).then(r => r.blob());
  // 触发下载：lynx_agent_history_<timestamp>.md
  downloadBlob(blob, `lynx_agent_history_${ts()}.md`);
}
```

**格式 Markdown**（示例）：

```markdown
# Lynx AI 对话记录

> 导出时间：2026-09-09 15:23
> 匿名标识：anon_4f3e2a1b...
> 共 12 个会话，47 条消息

---

## 1. 底特律近 30 天负向痛点 top5
**页面**：dashboard　**创建**：2026-09-08 15:23　**消息数**：4

**[15:23:01] 👤 用户**
底特律近 30 天负向评论里玩家最常抱怨什么？

**[15:23:08] 🔧 工具调用**
- `query_topics` (sentiment=negative, level=L2)

**[15:23:12] 🤖 助手**
近 30 天底特律负向评论中，主要痛点 top 5：
1. **剧情分支僵硬**（L2=剧情设计，占 23%）：「...」

---
```

---

## 6. 阶段拆解与验收

| 阶段 | 交付 | 验收 |
|---|---|---|
| 1 | 本文档 + 00-index 登记 + AGENTS.md 版本记录 | 文档可执行 + 决策表 22 项锁定 |
| 2 | DB schema：`agent_sessions`（含 `anon_user_id`） + `agent_messages` + FK CASCADE；init_db 自动建表 | smoke + db 存在性测试 |
| 3 | FastAPI `/api/agent/chat` SSE 端点 + DeepSeek-V4-Flash 接通 + 4 个 tool 封装 | curl 流式响应有 token / 调一次 tool 后正确返回 / pytest mock 5 例 |
| 4 | `/api/agent/sessions` CRUD + `/api/agent/export`（按 `X-Anon-User-Id` 隔离） | 列表/导出隔离测试；分页正确 |
| 5 | `config/agent/tools.yaml` + 4 个 Python tool 实现 + skill 系统 + 3 个范例 skill YAML | 单元测试 4+3 例；skill 自动注入 smoke 1 例 |
| 6 | 前端 `#/agent` 一级页（pages/agent.js）：左侧历史栏 + 主对话窗口 + 冷启动示例（白名单固定） + 导出按钮 | `/#/agent` 可访问；冷启动示例来自白名单前 2 款游戏 + 1 个视频 |
| 7 | 前端全局抽屉（agent-drawer.js）：右下 sparkle 浮动球 + 向左上铺开 + 流式渲染 + 一键引用 + anon_id 拦截器 | 任意页面打开抽屉；token 实时显示；tool_call 状态正确；悬浮视觉感 |
| 8 | 5 个 page 暴露 `__pageAgentContext` + 顶栏 nav 加 AI 一级项 + sparkle icon + 默认首页改 agent | dashboard/compare/bilibili/data/admin 全部设置；nav 顺序正确 |
| 9 | 速率限制中间件（60 req/min/IP）+ 公开端点鉴权标记 | 60/min 触发 429；测试 2 例 |
| 10 | `scripts/ops/prune_agent_history.py` + 计划任务 `VOC-Local-Agent-Prune`（03:30 cron）注册 | 手动跑 prune 正确级联删；dry-run 通过；schtasks 注册成功 |
| 11 | pytest 全量回归（≥ 150 例全绿） + smoke + uvicorn 重启 + AGENTS.md 版本记录 | §6 健康检查 8 条全过 |

**预估总工时**：5-6 天（按工程师单人节奏，比原 4-5 天多 1 天用于 `anon_user_id` + 30 天裁剪 + 冷启动白名单派生）

---

## 7. 风险与已知取舍

| 风险/取舍 | 说明 | 对策 |
|---|---|---|
| LLM token 成本不可控 | 用户长上下文（带 50 条评论）每轮 5-10k tokens | tool 返回摘要而非全量；前端塞附件前压缩 |
| 流式响应中断 | 网络抖动/客户端关闭 → 当前轮未完成 | 后端按 session 状态 partial save；前端 EventSource 自动重连同一 session_id |
| tool 调用死循环 | LLM 误判循环调同 tool | tool 返回带 `meta.tool_calls_count`；超 5 轮强制结束 + 返回 "已尽力" 提示 |
| 公开端点被滥用 | 无鉴权 → 被白嫖 LLM token | IP 速率限制（首版 60/min）；观察 1 周后调阈值；超阈值 429 + GH 邮件告警 |
| 抽屉 UI 性能 | 长对话 100+ 消息 → DOM 累积 | 滚动容器做虚拟滚动（首版可不做，限单 session 50 条消息） |
| skill 误匹配 | LLM 用了 tool 但 skill 描述不准确 | skill 描述要具体（"**当用户问 X 且 tool Y 返回 Z 时使用**"）；每周 review |
| P2 search_docs 召回率低 | 5 个文档全文 grep → 命中噪声大 | 首版够用（docs 总共 < 1MB）；后续换 embedding 检索（用现有 `comment_embeddings` 表同样的 bge-small-zh-v1.5） |
| 与 DSH 关系 | 文档不写 DSH 集成路径 | 用户已决策走方案 B（不引入 DSH）；此风险不适用 |
| 与静态快照部署冲突 | `/api/agent/*` 是动态端点，静态快照不含 | 静态版前端 shim 在 `agent-drawer.js` 检测到 `STATIC_SNAPSHOT` 标记时**隐藏浮动按钮**；提示"AI 功能仅在实时版可用" |
| **anon_user_id 跨设备失效**（2026-09-09 新增） | localStorage 清空/换设备/换浏览器 = 失去历史 | 文档告知"anon_user_id 是本机标识"；导出兜底；未来登录态平滑接管 |
| **30 天裁剪误删**（2026-09-09 新增） | cron 异常把活跃会话删了 | 裁剪只看 `created_at`（不用 `updated_at`），用户每天活跃的会话仍可保 30 天；测试覆盖裁剪逻辑；prune 跑前后输出 rowcount 日志 |
| **冷启动示例提到隐藏游戏**（2026-09-09 新增） | 若 derive 白名单逻辑出错可能展示被隐藏的游戏 | 从 `/api/games/meta` 取（已经过滤 `visible!=false`），二次校验 `(g.total || 0) > 0` 兜底 |

---

## 8. 配置示例（落地参考）

### 8.1 `config/agent/system_prompt.txt`

```
你是「灵听 Lynx · 原声分析助手」，专门帮助读者分析 Steam / B 站玩家评论数据。

你可以调用以下工具：
{tool_schemas}

输出风格：
- 直接给答案，不要先说"我来帮你查..."
- 引用数据时标注来源（如"近 30 天底特律负向评论中..."）
- 数字用阿拉伯数字，比例用 %
- 玩家引用用「」包裹，最多引 5 条

约束：
- 不知道就说不知道，不要编造数据
- 不要重复工具返回的原始 JSON，加工成可读结论
- 用户问"为什么"时给原因；问"多少"时给数字
```

### 8.2 `config/agent/tools.yaml`

见 §4.2。

### 8.3 `.env` 新增

```bash
# Agent LLM（与离线打标同源）
DEEPSEEK_API_KEY=sk-xxx          # 已有
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1   # 已有
DEEPSEEK_MODEL=deepseek-v4-flash                 # 已有

# Agent 速率限制（可选；默认 60 req/min/IP）
AGENT_RATE_LIMIT_PER_MIN=60

# Agent 对话保留天数（可选；默认 30 天，0 = 永久保留）
AGENT_RETENTION_DAYS=30
```

**无需新增 API key**，复用现有 DEEPSEEK 凭据。

---

## 📋 版本记录

| 更新时间 | 内容 | 原因 |
|---|---|---|
| 2026-09-08 | 初版：方案 B 落地蓝图（FastAPI `/api/agent/*` + SSE + function calling + 4 个 tool + skill 系统 + 抽屉 UI + 全局记录中心） | 工程师确认走自写路径（抽屉 UX + 跨页联动 + 全局记录要求排除 DSH iframe 方案）；原声分析 Agent 设计蓝本 |
| 2026-09-09 | **UX 三项 + 数据隐私 + 保留策略重构**：①抽屉 UI 改为"悬浮球 + 向左上铺开"（inset 32/80px，大圆角阴影，scale+opacity 动画）；②AI 记录中心从 `#/agent-history` 二级页提升为 `#/agent` 一级页面（左侧历史栏 + 主对话窗口）；③AI 作为顶栏 nav 左起第一项 + sparkle icon（候选 B）+ 默认首页改为 agent；④**anon_user_id UUID 匿名标识**（localStorage + 后端列 + API 隔离）；⑤30 天滚动裁剪（03:30 cron，FK CASCADE）；⑥`/api/agent/export` Markdown 导出（按 anon_user_id 严格隔离）；⑦冷启动示例改为从"展示"白名单固定前 2 款游戏 + 1 个视频派生（不再每次刷新换） | 工程师 2026-09-09 UX 反馈："原抽屉贴边太死板" + "AI 应是平台门面" + "需要匿名但能保留自己历史" + "示例要稳定"；新增决策 #16-#22 |