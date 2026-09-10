# 公网部署方案选型（5 方案对比）

> **用途**：回答「把这个项目部署到公网有哪几种走法、各自要花多少钱和多大力气」。本文档是**选型评估**，不是操作手册——落地步骤见 `SELF_HOSTED_VPS_DEPLOYMENT.md`（方案 ①）。
>
> **关联文档**：
> - 形态 A 部署指南：[SELF_HOSTED_VPS_DEPLOYMENT.md](./SELF_HOSTED_VPS_DEPLOYMENT.md)（方案 ① 的 9 步操作手册）
> - Web 实时看板架构：[WEB_DASHBOARD.md](./WEB_DASHBOARD.md)（被部署的对象：FastAPI + 原生 SPA）
> - 自动化采集流水线：[AUTOMATION_PIPELINE.md](./AUTOMATION_PIPELINE.md)（方案 ③ 依赖的 GitHub Actions 链路）
> - 总路线图：[../plan/DEVELOPMENT_PLAN.md](../plan/DEVELOPMENT_PLAN.md)
> - 字段与存储设计：[DATA_FIELDS.md](./DATA_FIELDS.md) / [DATA_STORAGE_DESIGN.md](./DATA_STORAGE_DESIGN.md)
>
> **最后更新**：2026-09-10
> **状态**：🟢 方案 ③ 已落地（2026-09-07，见 [STATIC_SNAPSHOT_DEPLOYMENT.md](./STATIC_SNAPSHOT_DEPLOYMENT.md)）；🟡 **方案 ① 于 2026-09-10 改为启用变体 ①b**（本机采集 + 推 DB + VPS 只读服务，含实时查询与 Agent 对话）——落地清单见 [SELF_HOSTED_VPS_DEPLOYMENT.md §11](./SELF_HOSTED_VPS_DEPLOYMENT.md)，**待外部资源到位**（国内轻量 VPS / 域名 / ICP 备案）

---

## 0. 一句话结论

> **两步走：先上方案 ③（静态快照 · 零服务器 · 零成本）做作品集门面，再上方案 ①（VPS 全栈自托管 · ¥0~120/年）做真实可用系统。方案 ④ 现在不要碰。**

| 时间 | 做什么 | 产出 | 成本 |
|---|---|---|---|
| 第一步 | 方案 ③：GH Actions 导出 JSON → Cloudflare Pages | 永不宕机、秒开、公网可分享的作品集链接 | ¥0 / 半天 |
| 第二步 | 方案 ①：Oracle Cloud 免费机 + Caddy + uvicorn + cron | 可实时查询、管理员能增删改采集任务、数据全私有 | ¥0 / 1~2 小时 |

---

## 1. 三条硬约束（决定哪些方案可行）

> 选型不是挑平台，是先看清这个项目的物理限制。任何方案必须先过这三关。

| # | 约束 | 具体表现 | 直接排除 |
|---|---|---|---|
| **C1** | **SQLite 单文件 + 每日写入** | `data/voc.db`（WAL 模式，约 45 MB）；cron 每日写、Web 服务实时读；**单写者语义** | Vercel / Netlify / 任何只读 FS 或多实例无共享盘的 serverless。只读展示可行，**写入不可行** |
| **C2** | **采集 + LLM 标注是长任务** | 单次 3~10 分钟（Steam 翻页 + GLM-5.3-Flash 标注），需出站 HTTPS 调 Steam / B站 / BigModel API | Cloudflare Workers（CPU 时长限制）、任何函数超时 < 5 min 的平台 |
| **C3** | **数据私有诉求** | 采集脚本、`.env` 里的 API Key、DB 全量数据不希望外泄 | DB 放第三方托管、公开 artifact（本项目已在 P6 中刻意规避，见 `AUTOMATION_PIPELINE.md §8`） |

**推论**：
- 能同时满足 C1 + C2 的，只剩**「有持久化磁盘的长驻进程」**——即 VPS 或带 volume 的 PaaS 容器。
- 只满足 C1（放弃写）的，才能走**纯静态**。
- C3 与「成本低」不冲突，但与「零运维」冲突（越托管，数据越不在自己手里）。

---

## 2. 五个方案

### 方案 ① · VPS 全栈自托管 ⭐ 推荐（真实可用系统）

```
公网用户 → Caddy :443（反代 + 自动 HTTPS）
              ↓ 127.0.0.1:8000（仅本机可达）
        uvicorn src.api.main:app
              ├─ 静态托管 product/web/（SPA，无 Node 构建链）
              ├─ /api/*          公开只读（访客免登录）
              └─ /api/admin/*    session 鉴权（任务 CRUD）
              ↓
        data/voc.db（WAL，权限 600）
              ↑ cron 每日写：daily_incremental_collect.py + bilibili run-due
```

| 维度 | 评分 | 说明 |
|---|---|---|
| 部署便捷 | ★★★☆☆ | 一次性 1~2 小时，`SELF_HOSTED_VPS_DEPLOYMENT.md` 有 9 步可照抄。**外加 `docker-compose.yml` 可把「环境一致性 + 迁移重装」从 2 小时压到 10 分钟** |
| 维护便捷 | ★★★☆☆ | 系统补丁、备份、日志自查；Caddy 自动续期证书省掉最大一块；周备份 + VACUUM 已有现成 cron 行 |
| 成本 | ★★★★★ | **¥0 起**（Oracle Cloud Always Free ARM 4C/24G，永久免费）；国内轻量 2C2G 约 ¥60~120/年；域名 ¥0~50/年 |
| 稳定性 | ★★★☆☆ | 单点无冗余；Oracle 免费机有「闲置回收」策略（需保活）；但**依赖最少，故障排查最直接** |
| 数据私有 | ★★★★★ | 唯一满分项：DB 只在本地磁盘，无任何 HTTP 出口暴露它 |

**变体 ①b — 本机采集 + 推 DB + VPS 只读服务（2026-09-10 确认启用）**
采集与标注继续留在本机（B 站风控 / 代理环境必须本地），每日 `wal_checkpoint → scp → 远端原子替换` 把 `data/voc.db` 推到 VPS；VPS 只跑 Caddy + uvicorn（实时查询 + Agent 对话，DB 只读消费）。VPS 规格可降到 2C2G 且**不装 ML 依赖**；代价是多一条同步链路（失败只记 ERROR，不阻塞采集判定）。
> 早期草案曾考虑「VPS 拉 GH Release」，但 `collect` job 已停用（切本地直采），故实际改为**本机直推**。

**变体 ①c — 停用 Streamlit**
Web 看板（FastAPI + SPA）已覆盖仪表盘能力，VPS 上可只起 uvicorn 一个服务，省 ~400 MB 常驻内存与一个 systemd unit。

---

### 方案 ② · PaaS 容器托管 + 持久卷（Railway / Render / Fly.io）

把项目打成镜像推上去，平台负责 OS、TLS、重启、扩缩容；挂一块 volume 放 `voc.db`。

| 维度 | 评分 | 说明 |
|---|---|---|
| 部署便捷 | ★★★★☆ | `git push` 或 Dockerfile 即部署，完全不用碰 Linux |
| 维护便捷 | ★★★★☆ | 平台管底层；但 SQLite 是别扭点（见下） |
| 成本 | ★★☆☆☆ | 约 **$5~10/月（¥420~840/年）**，volume 另计 $0.1~0.25/GB。免费额度基本已取消或不够 |
| 稳定性 | ★★★☆☆ | 平台 SLA 尚可，但低配层有**休眠冷启动**（首访 10~30s），对看板体验伤害明显 |
| 数据私有 | ★★☆☆☆ | DB 在第三方卷上，出问题时排查手段有限 |

> ⚠️ **已知坑**：Render 的 Cron Job 是**另起容器**，与 Web Service 挂同一个 volume 时会与常驻进程争抢 SQLite 写锁；Railway 同理。**真走这条路建议顺手换 Postgres**（改造量 +1 天）。

**适合谁**：明确不想学 Linux 运维、且愿意月付 ¥50 左右。

---

### 方案 ③ · 静态化：Pages + 每日快照 ⭐ 推荐（作品集门面）

```
GitHub Actions（现有 P6 流水线）
  ├─ 每日采集 + 标注 → 写 data/voc.db
  └─ 【新增一步】导出 JSON 快照 → 推 Cloudflare Pages / GitHub Pages（全球 CDN）
```

| 维度 | 评分 | 说明 |
|---|---|---|
| 部署便捷 | ★★★★★ | 现有流水线已跑到「采集 + 写 DB」，只需加一个导出脚本 + 发布步骤 |
| 维护便捷 | ★★★★★ | 零服务器、零补丁、零账单 |
| 成本 | ★★★★★ | **¥0** |
| 稳定性 | ★★★★★ | CDN 静态资源，几乎不可能挂 |
| 数据私有 | ★★☆☆☆ | 快照公开（内容本身都是平台公开评论，风险可控；但**不能放任何私密字段**） |

**代价**：
- ❌ **无实时查询**——页面只能按预聚合维度切换，不能任意筛选
- ❌ **无写操作**——系统管理页（任务 CRUD）失效，或仅保留 UI 不可提交
- ⚠️ 交互自由度大幅下降（下钻、时间序列需预生成所有组合）

**适合谁**：这是**求职作品集门面**的最佳解——打开秒开、永不宕机、不花钱、可直接贴进简历。现有的 B 站单视频看板（`product/build_bilibili_video.py` 内联 JSON 生成单文件 HTML）本质上就是这个模式的雏形，可复用其导出思路。

---

### 方案 ④ · Serverless 重构：Cloudflare Workers + D1（长期演进，现在不做）

前端 SPA 放 Pages，API 迁 Workers，`voc.db` 迁 D1（SQLite 兼容），cron 用 Cron Triggers，采集逻辑改在 Worker 内跑。

| 维度 | 评分 | 说明 |
|---|---|---|
| 部署/维护 | ★★★★★ | 改造完成后几乎零运维 |
| 成本 | ★★★★★ | 免费额度内 ¥0 |
| 稳定性 | ★★★★★ | 边缘网络 |
| 数据私有 | ★★★☆☆ | 数据在 CF，但访问面可控 |
| **改造量** | ❌ **1~2 周** | 见下方风险 |

> ❌ **不建议现在做**，原因：
> 1. `src/storage/db.py` 需从 SQLAlchemy 重写为 D1 HTTP API，**现有 78 例 pytest 几乎全部失效**
> 2. 本地 bge-small-zh 向量化与本地 BERT **无法在 Workers 运行**（两个已完成的模块直接废掉）
> 3. 采集脚本受 Workers CPU 时长与内存限制，Steam 翻页 + 批量标注很可能超限
> 4. 管理员鉴权（session）需重做为无状态方案
> 5. 会严重拖住 P9 主线（L3.5 微话题聚类 / PEDM 负向观点）

**定位**：作为 v2.0 演进方向记录在案即可。

---

### 方案 ⑤ · 混合：静态前端上 CDN + API/DB 留 VPS（方案 ① 的增强）

把 `product/web/` 部署到 Cloudflare Pages（免费 CDN），API 与 DB 留在 VPS，前端跨域请求。

| 维度 | 评分 | 说明 |
|---|---|---|
| 部署便捷 | ★★★☆☆ | 两套部署流程，需配 CORS |
| 维护便捷 | ★★★☆☆ | 前端改动要单独发布一次 |
| 成本 | ★★★★★ | 静态流量全卸载到 CDN，VPS 可降到 1C1G，¥0~120/年 |
| 稳定性 | ★★★★☆ | 静态资源抗峰值；API 仍是单点 |
| 数据私有 | ★★★★★ | DB 不出 VPS |

**何时升级到 ⑤**：看板访问量上来，或 `product/web/vendor/echarts` 大文件吃掉 VPS 出口带宽时。

---

## 3. 横向对比总表

| 方案 | 部署便捷 | 维护便捷 | 成本/年 | 稳定性 | 数据私有 | 改造量 | 实时查询 | 管理页可写 |
|---|---|---|---|---|---|---|---|---|
| ① VPS 全栈 ⭐ | ★★★☆☆ | ★★★☆☆ | **¥0~120** | ★★★☆☆ | ★★★★★ | 0（照文档） | ✅ | ✅ |
| ①b VPS 只读 + 本机推 DB | ★★★☆☆ | ★★★☆☆ | ¥0~120 | ★★★☆☆ | ★★★★★ | 0.5 天 | ✅ | ❌（VPS 是只读副本） |
| ② PaaS 容器 | ★★★★☆ | ★★★★☆ | ¥420~840 | ★★★☆☆ | ★★☆☆☆ | 0.5 天 | ✅ | ⚠️ 需解决并发写 |
| ③ 静态快照 ⭐ | ★★★★★ | ★★★★★ | **¥0** | ★★★★★ | ★★☆☆☆ | 0.5 天 | ❌ 仅预聚合 | ❌ |
| ④ Workers + D1 | ★★★★★ | ★★★★★ | ¥0 | ★★★★★ | ★★★☆☆ | **1~2 周** | ✅ | ✅ |
| ⑤ CDN + VPS | ★★★☆☆ | ★★★☆☆ | ¥0~120 | ★★★★☆ | ★★★★★ | 1 天 | ✅ | ✅ |

> 评分口径：★ 越多越好；成本栏 ★ 越多表示越便宜。

---

## 4. 决策记录（2026-09-04 初版 · 2026-09-10 追加）

| # | 决策点 | 结论 | 理由 |
|---|---|---|---|
| 1 | 主路线 | **方案 ③（先）+ 方案 ①（后）组合拳**，不做二选一 | ③ 给「永不宕机的作品集链接」，① 给「真实可用的系统」，两者解决的是不同问题，且③不阻塞① |
| 2 | 方案 ④（Workers + D1） | **排除，v2.0 再议** | 改造量 1~2 周且会让 78 例测试与本地向量化/BERT 模块失效；会拖住 P9 主线 |
| 3 | 方案 ②（PaaS） | **备选，非推荐** | 月付 ¥35~70 换取「不碰 Linux」；但 SQLite 在 PaaS 上是别扭点，且免费层休眠冷启动伤害体验 |
| 4 | 方案 ① 是否加 Docker | **建议加** | 一次性成本极低（一个 `docker-compose.yml`），换来环境一致性与 10 分钟迁移/重装能力 |
| 5 | 方案 ① 是否保留 Streamlit | **VPS 上建议停用（变体 ①c）** | Web 看板已覆盖仪表盘能力；Streamlit 常驻占 ~400 MB 且是额外攻击面 |
| 6 | 方案 ③ 快照是否含全量明细 | **只放聚合结果，不放原始评论全量** | 快照公开，遵循最小暴露原则；原始评论保留在私有 DB |
| 7 | 何时启动 | ~~暂不开发，等 P9 阶段 2/3 落地后~~ → **2026-09-10 提前启动**（③ 已上线，①b 进入落地准备） | 静态门面已就绪、Agent 已上线，「公网可访问」诉求提前于 P9 |
| 8 | **2026-09-10 追加**：VPS 形态采用哪个变体 | **变体 ①b**（本机采集 + 推 DB + VPS 只读服务） | B 站风控依赖本机 buvid/代理（搬 VPS 会重踩 412）；本地直采 + 哨兵已跑稳，不重复建设；VPS 最轻（不装 ML）；DB 只在两台受控机间 SSH 传输。域名新买 + 国内节点需 ICP 备案；Agent 按「公开 + 限流」小范围内测 |

---

## 5. 落地前置检查（真要动手时逐条核对）

- [ ] `ADMIN_PASSWORD_HASH` 已生成（`python scripts/ops/hash_admin_password.py <密码>`）
- [ ] `SESSION_SECRET_KEY` 已生成（`python -c "import secrets;print(secrets.token_hex(32))"`）——**缺失时 `src/api/main.py` 会 fail-closed 拒绝启动**
- [ ] `.env` 权限 600，且**未**提交进 git
- [ ] `data/` `logs/` `backups/` 目录权限 700
- [ ] 只装 `requirements-dashboard.txt`，**不装 ML 依赖**（VPS 无 GPU 且用不到）
- [ ] WAL 模式下备份前先 `PRAGMA wal_checkpoint(TRUNCATE)`，否则备份缺最近数据
- [ ] cron 用 `voc` 用户 crontab，不用 root（防 DB 文件权限被污染）
- [ ] ufw 只开 80/443 + 自定义 SSH 端口；SSH 禁密码登录
- [ ] 方案 ③ 的快照导出脚本需登记到 `scripts/README.md`

---

## 📋 版本记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-09-10 | **方案 ① 定为变体 ①b 并进入落地准备**：本机采集 + `push_db_to_vps.ps1` 推 DB + VPS 只读服务（Caddy + uvicorn，含实时查询与 Agent 对话）；VPS 用国内轻量、域名新买（国内节点需 ICP 备案）、Agent 公开 + 限流小范围内测。同步修正本文档：①b 定义（原「VPS 拉 GH Release」已不成立，`collect` job 已停用）、①b 对比行、决策 7 解冻 + 追加决策 8；落地清单见 [SELF_HOSTED_VPS_DEPLOYMENT.md §11](./SELF_HOSTED_VPS_DEPLOYMENT.md) | P11 收尾时同步文档：把「计划外已做 + 2026-09-10 部署决策」补进选型文档，消除与 DEVELOPMENT_PLAN 的状态不一致 |
| 2026-09-07 | **方案 ③ 落地**：`scripts/ops/export_static_snapshot.py`（三页预聚合快照，复用 service.py 聚合层）+ `api.js` 静态 shim（manifest 路由表，实时模式零影响）+ `publish_static_snapshot.ps1`（EdgeOne Pages）+ daily `--publish-snapshot`（默认关闭）。决策 7 解冻：数据链路已切本地直采，导出走本地脚本而非 GH Actions；托管平台按工程师确认用 EdgeOne Pages（非文档原定的 Cloudflare Pages）。操作手册：[STATIC_SNAPSHOT_DEPLOYMENT.md](./STATIC_SNAPSHOT_DEPLOYMENT.md) | 工程师启动部署开发 |
| 2026-09-04 | 初版：5 方案（VPS 全栈 / PaaS 容器 / 静态快照 / Workers+D1 / CDN+VPS）+ 3 条硬约束 + 7 条决策记录 + 落地前置检查 | 工程师提出「部署到公网」需求，先做选型评估、**暂不开发** |
