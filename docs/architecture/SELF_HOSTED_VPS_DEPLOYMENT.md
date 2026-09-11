# 形态 A · 自托管 VPS 部署（公网可访问 / 数据全私有）

> **用途**：让团队外用户能通过公网 URL 查询仪表盘（图表 + 视图），但**采集代码 / 标注脚本 / 数据库 / API Key / 中间产物全部留在团队控制的 VPS 上**，任何人无法下载或越权访问。
>
> **关联文档**：
> - 部署方案选型（为什么选形态 A / 还有哪几种走法）：[DEPLOYMENT_OPTIONS.md](./DEPLOYMENT_OPTIONS.md)
> - 总路线图：[plan/DEVELOPMENT_PLAN.md](../plan/DEVELOPMENT_PLAN.md)
> - 自动化采集流水线：[AUTOMATION_PIPELINE.md](./AUTOMATION_PIPELINE.md)
> - 字段与存储设计：[DATA_FIELDS.md](./DATA_FIELDS.md) / [DATA_STORAGE_DESIGN.md](./DATA_STORAGE_DESIGN.md)
> - 安全与隐私声明：见本文 **§5 安全加固清单（必查）** 与根目录 `AGENTS.md` **§3 必守的工程红线**
>   （2026-09-11 修正死链：原引用的 `docs/SECURITY.md` 从未存在）
>
> **最后更新**：2026-09-11
> **状态**：🟢 内测已上线——VPS 跑 `voc-web.service`（uvicorn `127.0.0.1:8000`，systemd 常驻 + 开机自启）+ Caddy `:8443` 反代，公网 `http://134.175.115.248:8443` 可访问；变体 ①b（本机采集 + DB 同步 + VPS 只读服务，见 §0.5）；域名 HTTPS 待 ICP 备案通过后切 §7B

---

## 0. 一句话总览

> **一台公网 VPS 同时承担两件事**：
> 1. **私有构建层**：跑 `cron` 触发 `daily_incremental_collect.py`（采集 + 分析 + 写 SQLite 单文件 DB）
> 2. **公网服务层**：跑 `streamlit run app.py`（绑 `127.0.0.1:8501`，由 `Caddy` 反代到 443 / 自动 HTTPS）
>
> **两件事在同一台机器，但 DB 文件权限 600、Streamlit 进程以非 root 用户运行**——攻击者最多打到 Streamlit 容器，**碰不到 DB 路径、拿不到 API Key、改不了数据**。

---

## 0.5 变体 ①b · 本机采集 + DB 同步 + VPS 只读服务（2026-09-10 决策确认，本次启用）

> 本文档下述 §1–§10 描述的是**方案 ① 全栈上云**（VPS 自己跑采集）。
> 本次实际采用 **变体 ①b**：采集与标注继续留在本机，VPS 只跑「读服务 + Agent」。
> 差异部分以本节为准，其余步骤（§3 准备、§5 安全、§6.5 Web 服务、§7 Caddy、§9 验收）仍适用。

### 架构

```
本机（已有，保持不动）                        VPS（本次新增）
──────────────────────────────              ──────────────────────────────
02:00 VOC-Local-Daily-Collect               Caddy :443（自动 HTTPS，备案后）
03:00 VOC-Local-Daily-Collect-Check         └─ uvicorn 127.0.0.1:8000（voc-web.service）
Steam / B站 采集 + LLM 标注                    ├─ SPA（product/web/，5 页 + Agent 抽屉）
（B站风控、代理环境必须留本地）                  ├─ /api/*        实时数据查询
        │ 写 data/voc.db（单一权威源）          ├─ /api/agent/*  Agent 对话（SSE 流式）
        │                                      └─ data/voc.db（600，只读消费）
        └── 每日同步（VACUUM INTO → scp → 远端原位 .backup()，见下）
```

### 为什么选 ①b（而不是 VPS 自己采集）

| 理由 | 说明 |
|---|---|
| B 站风控 | 采集依赖本机 buvid 会话 + 本机代理（`127.0.0.1:7877` 环境）；搬 VPS 会重踩 412（2026-09-05 结论） |
| 已有稳定链路 | 本地直采 + 补采哨兵已跑通（2026-09-07），不重复建设 |
| VPS 最轻 | 只装 `requirements-dashboard.txt`（**不装 ML**），常驻 ~300–500 MB，2C2G 足够 |
| 数据私有 | DB 只在两台受控机器间 SSH 传输，无第三方面板 |

### DB 同步设计（2026-09-11 已实现 + 实测）

- 脚本：`scripts/ops/push_db_to_vps.ps1`（本地快照 → scp → 远端**原位**回灌）
  1. **本地**：`PRAGMA wal_checkpoint(TRUNCATE)` + **`VACUUM INTO`** 出一份一致性快照
     （比直接 scp 主库安全：不长时间锁库、顺带整理碎片；实测 117.2 MB → 116.9 MB）
  2. **校验**：本地 SHA256 → `scp` 到 `/tmp/voc.db.new` → 远端比对 SHA256 +
     `PRAGMA integrity_check` + `comments` 条数 > 0（任一不过 → 远端**分毫不动**）
  3. **远端**：`sqlite3.Connection.backup()` 把快照**回灌进现有的 `data/voc.db`**
- ⚠️ **与原设计的偏离（有意为之，务必别改回 `mv`）**：原设计写"远端 `mv` 原子替换"，
  实测**对本站不安全**——`voc-web.service` 常驻且持有 SQLAlchemy 连接池，`mv` 只换
  inode，池里那些已打开的连接会**永远继续读那个被 unlink 的旧文件**（不报错、
  只是数据永远不更新，属于最难查的静默陈旧）。`sqlite3 .backup()` 是在**原文件内逐页
  重写**，已在服务的读者能立刻看到新数据；也不需要重启服务（并发写冲突由
  `busy_timeout=60000` 兜住，WAL 下读写可并行）。
- 失败语义：脚本 `exit 1` 且远端不改；`daily_incremental_collect.py` 只记 warning，
  **不影响采集退出码**——本机始终是唯一权威源，VPS 只是展示端，宁可 VPS 旧一天，
  也不能让 02:00 采集被判失败（与 `--publish-snapshot` 同款解耦原则）
- ⚠️ **单向语义的必然结论：VPS 必须只读**（2026-09-11 收口）。既然是"整库覆盖"，VPS 上
  任何写入（admin 页建任务、点「立即采集」）都只会被下一次推送抹掉；而「立即采集」还会
  **真的在 VPS 上跑 pipeline + 花 token**，且 VPS 无生产标注器 Key → `get_analyzer()`
  回落到 `deepseek` → 写脏 `analyzer_version`。即"看着能采，其实白采还有害"。故做了
  **代码级收口**（不靠人记住别点），见 §5.5.3「展示端（`DISPLAY_ONLY`）」。
  反向（VPS → 本地）**没有任何通道**：线上改的任务/线上产生的 Agent 会话都只存在于 VPS，
  下一次推送即被本地版本覆盖（实测：推送后 VPS `agent_sessions` 比本地多出的那些会消失）。
- 触发时机：**并入 02:00 采集链路末尾**（`daily_incremental_collect.py --push-db`）。
  不另开计划任务的理由：推送必须等采集**全部跑完**，同进程内顺序天然成立，
  另开任务反而要自己造时序守卫。如需当日更新再另加 10:00/18:00 两次（先跑稳再决定）
- 参数：`-DbPath` / `-Remote` / `-RemoteDb` / `-Identity`（默认 `~/.ssh/k_lynx_web.pem`）；
  `-DryRun` 只出本地快照不联网；`-RestartService` 默认关（原位回灌不需要重启）
- 传输安全：复用部署用 SSH 密钥，不新增密码/端口暴露；`data/covers/` 不随库同步，
  新增封面目标需手动补传

### 本次确认的 4 项决策（2026-09-10）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | VPS 来源 | **国内轻量应用服务器**（获取方式与备案见 §3.3） |
| 2 | 数据通道 | **①b**：本地采集 + 推 DB 到 VPS |
| 3 | 域名 | **新买**（需实名；国内节点须 ICP 备案，见 §3.3） |
| 4 | Agent 访问 | 按既有决策「**公开 + 限流**（60 req/min/IP）」，先**小范围内测**，观察后再调阈值/加口令 |

---

## 1. 架构图

> ```
>                     公网（Internet）
>                          │
>                          ▼  HTTPS（Let's Encrypt 自动证书）
>                  ┌──────────────────┐
>                  │   Caddy :443     │  反向代理 + TLS 终止 + 自动续签
>                  │   （root 进程）  │
>                  └────────┬─────────┘
>                           ▼  127.0.0.1:8501（仅本机）
>                  ┌──────────────────┐
>                  │ Streamlit 服务   │  以 `voc` 用户运行
>                  │ （voc 用户进程） │  渲染图表 + 读 DB
>                  └────────┬─────────┘
>                           ▼  sqlite:///data/voc.db（权限 600）
>                  ┌──────────────────┐
>                  │   data/voc.db    │  单一权威源
>                  │ （voc:voc 600）  │
>                  └────────▲─────────┘
>                           │  每日 cron 写
>                  ┌────────┴─────────┐
>                  │   cron 任务      │  以 `voc` 用户跑
>                  │  daily_increm... │  调 DeepSeek API + Steam/B站采集
>                  └──────────────────┘
>                           │
>                           ▼  仅出站 HTTPS
>                  Steam API / B 站 Web API / DeepSeek API
> ```
>
> **关键边界**：
> - 公网只能命中 Caddy 的 443 端口；SSH 22 端口走密钥 + IP 白名单（推荐）或厂商控制台
> - Streamlit 监听 `127.0.0.1`，Caddy 之外没有任何路径能直达
> - DB 文件、`.env`、日志均限 `voc` 用户访问，root 也只读不写
> - **没有 GitHub Release / 公开 artifact**——彻底切断"任何人 wget DB"的路径

---

## 2. 为什么选形态 A

> **与"形态 B/C"对比**（决策依据）
>
> - **形态 B（托管 DB + 公开应用）**：DB 在第三方托管平台，再小心也有"账号被攻 → DB 全量泄漏"的风险，且多一份网络依赖
> - **形态 C（Serverless + 私有 R2）**：冷启动延迟 + DB 不在本地，调试和应急都不直观
> - **形态 A（本方案）**：DB 永远在这台机器的磁盘上，没有任何 HTTP 出口暴露它；攻击面 = Caddy 反代 → Streamlit 容器 → 只读视图，**最窄**
>
> **成本**：
> - Oracle Cloud Always Free ARM VPS（4 CPU / 24 GB RAM）：**永久免费**
> - 域名（可选）：¥0-50/年（freenom / eu.org 免费 / .cn 约 ¥30）
> - 总计：**¥0 起**，上限 ¥50/年

---

## 3. 准备工作

### 3.1 必备清单

- **一台公网 VPS**：推荐 **Oracle Cloud Always Free ARM**（永久免费，4 核 / 24 GB / Ubuntu 22.04 / 24.04）
- **一个域名**（可选但强烈推荐）：否则用户访问 `http://<IP>`，无 HTTPS，且 IP 暴露反代错误信息
- **SSH 密钥对**（本机已生成 `~/.ssh/id_ed25519.pub`）
- **7 个 secrets**（任何途径都**不要**写进 git）：
  - `STEAM_API_KEY`（[申请](https://steamcommunity.com/dev/apikey)）
  - `DEEPSEEK_API_KEY`（[申请](https://platform.deepseek.com/)）
  - `BILIBILI_SESSDATA`（可选，普通视频不配也能采）
  - **GitHub PAT**（**只读权限** `repo:read`，用于 `git clone` 私有仓库；首次 `git clone` 后可丢掉）
  - `ADMIN_PASSWORD_HASH`（Web 看板管理员密码哈希，生成：`python scripts/ops/hash_admin_password.py <密码>`）
  - `SESSION_SECRET_KEY`（Web 看板 session 签名密钥，生成：`python -c "import secrets;print(secrets.token_hex(32))"`）
  - 数据库密码：本方案是 SQLite 单文件，**不另设 DB 密码**——靠文件系统权限（600）保护

> ⚠️ 2026-09-02 起形态 A 可启用 **Web 实时看板**（FastAPI + 原生 SPA，见 `docs/architecture/WEB_DASHBOARD.md`）：Caddy 反代 `127.0.0.1:8000`（uvicorn 服务），Streamlit（8501）可保留或停用。访客免登录看图表，管理员经 `/api/auth/login` 登录后可增删改采集任务。

### 3.2 推荐 VPS 规格

| 项 | 最小 | 推荐 | 备注 |
|---|---|---|---|
| CPU | 1 核 | 2 核+ | Oracle ARM 4 核免费 |
| RAM | 1 GB | 2 GB+ | 仪表盘常驻 ~400 MB，留余量给 DB 缓存 |
| 磁盘 | 20 GB | 40 GB+ | DB 当前 45 MB，备份 5 份约 1 GB，日志约 5 GB |
| 系统 | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS | 装 Python 3.11/3.12 方便；ARM 选 Ubuntu |
| 出口带宽 | 10 Mbps | 50 Mbps+ | 仪表盘主要是静态图表，需求不大 |

### 3.3 国内轻量服务器怎么获取 + 域名 / 备案（2026-09-10 决策）

**购买路径（腾讯云轻量应用服务器 Lighthouse，与 EdgeOne 同账号，控制台统一管理）**

1. 登录 [腾讯云控制台](https://console.cloud.tencent.com/) → 搜索「轻量应用服务器」→ 新建实例；
2. 选**地域**（关键，见下方备案说明）：上海 / 广州 / 北京（国内）或 中国香港 / 新加坡（免备案）；
3. 选**镜像**：Ubuntu 24.04 LTS（与本文档步骤一致）；
4. 选**套餐**：2 核 2G / 40 GB SSD / 3–6 Mbps 峰值带宽（本项目常驻 ~500 MB，绰绰有余；DB 现 129 MB、日增 2–10 MB）；
5. 设 root 密码或直接绑定 SSH 密钥（推荐密钥 + 密码登录关掉，见 §5）；
6. 购买时长：选 **1 年**（新用户首年常见 ¥60–120，续费约 ¥200–300/年）。

> 同价位替代：阿里云轻量应用服务器（流程几乎一致）。有既有云账号的优先用已注册那家，省一次实名。

**地域怎么选（决定能否立刻上线）**

| 地域 | 备案要求 | 上线速度 | 国内访问延迟 | 建议 |
|---|---|---|---|---|
| 上海 / 广州 / 北京 | **必须 ICP 备案**（未备案 80/443 被阻断，域名无法访问） | 备案约 1–3 周 | 最快（10–30 ms） | 追求最优体验、能等备案 |
| 中国香港 / 新加坡 | **免备案**，即买即用 | 当天 | 30–80 ms | **推荐先上线**（内测阶段够用），后续要提速再迁国内 + 备案 |

> 备案期间仍可用 `http://<IP>:8443` 之类非标端口自测（非标端口不受 80/443 阻断影响），但**正式域名访问必须等备案通过**。

**域名购买与备案（决策：新买）**

1. 域名：腾讯云 DNSPod 或阿里云万网购买，`.cn` 约 ¥30/年、`.com` 约 ¥60–80/年；
2. **域名实名认证**（必须，1–3 个工作日）——备案的前置条件；
3. 若选国内节点：在腾讯云「备案」控制台提交 **ICP 备案**（个人可备案；轻量实例可申请备案服务码/授权码），约 1–3 周；
4. 备案通过后：DNS 加 A 记录指向 VPS IP → Caddy 用该域名自动申请 HTTPS 证书（§7）。

**成本小结（首年）**：轻量 ¥60–120 + 域名 ¥30–80 = **约 ¥100–200/年**（免备案走香港节点可当天上线）。

**购买界面逐项选择（2026-09-10 对照实际控制台）**

| 界面项 | 选什么 | 为什么 |
|---|---|---|
| 镜像类型 | **基于操作系统镜像** | ⚠️ 不要选「使用应用镜像」（Hermes Agent / WordPress 等预设栈）——本项目要自己装 Ubuntu + Python + Caddy，选应用镜像等于预装一堆用不上的东西，后续还得重装系统 |
| 操作系统 | **Ubuntu 24.04 LTS** | 本文档 §4 部署步骤全按 Ubuntu 写 |
| 地域 | **中国香港**（免备案、当天上线）或 **上海/广州**（需 ICP 备案、延迟最低） | 见上文地域对比；内测建议香港。⚠️ 界面若显示「企业」等专区标签，点开确认可选到目标地域 |
| 套餐 | **2 核 2G**，系统盘 ≥40 GB SSD，带宽 ≥3 Mbps，月流量 ≥2000 GB | 本项目常驻 300–500 MB、DB 129 MB、日增 2–10 MB；4 核 8G 属浪费。同价位优先选磁盘/带宽更大的档 |
| 时长 | **1 年** | 新客折扣最大（首年常见 ¥60–120）；按月买性价比差 |
| 登录方式 | **SSH 密钥对**（新建密钥 → 下载并妥善保存私钥） | 最安全，直接满足 §5「SSH 仅密钥登录」红线；也可先设密码，部署时再换密钥 |
| 实例名称 | 如 `voc-web` | 便于后续多实例区分 |
| 自动续费 | 可选；不确定长期用则先**不勾** | 避免忘记后自动扣费 |

> 下单后请提供：**公网 IP**、**SSH 端口**（默认 22）、**登录用户**（Ubuntu 镜像默认 `ubuntu`）、SSH 公钥是否已绑定——即可进入 §4 部署。

**下一步无需等你完全准备好**：VPS 一到手即可按 §4 步骤 1–5 初始化（装环境 / 建 voc 用户 / 配 .env），域名与备案可并行推进；Caddy 证书在域名生效后一步到位。

### 3.4 香港 → 大陆 迁移路径（2026-09-10 确认）

> 结论：**要在大陆地域跑，必须另购一台大陆实例（地域不可直接变更），但无需重装环境**。

| 问题 | 答案 |
|---|---|
| 能改地域吗 | ❌ 轻量实例地域固定，不支持原地切换 |
| 要再花钱吗 | ✅ 需再买一台大陆实例；香港那台按下方处置 |
| 要重新配环境吗 | ❌ 不必——官方支持**自定义镜像跨地域复制**：香港实例制作自定义镜像 → 复制到大陆目标地域 → 用该镜像新建实例 |
| 香港那台怎么办 | 购买后 **5 天内可无理由自助退还**（每个主体 × 每个套餐类型**仅限首次**）；超过 5 天则**到期不续**（损失可控：约 ¥102/年） |
| 域名要换吗 | ❌ 不变；只需改 DNS 解析到新 IP（切换前把 TTL 调短，如 300s）；Caddy 在新机自动重签证书 |
| 备案要重做吗 | 大陆节点**首次上线必须 ICP 备案**（这步省不掉）；同账号同域名后续换机器走「接入备案/变更接入」，比首次快得多 |

**两条采购策略（按能否等备案选）**

- **策略 A（省钱，只买一台）**：直接买**大陆**节点 + 立即提交 ICP 备案；备案期间（1–3 周）用 `http://<IP>:8443` 做内测，备案通过后解析域名 + Caddy 自动 HTTPS。
- **策略 B（当天可用，内测推荐）**：先买**香港**节点，当天上线给内测用户；同时并行提交备案；备案通过后按「自定义镜像跨地域复制」迁到大陆，香港机 5 天内退或到期不续。
  - 迁移耗时：脚本化部署后约 **30–60 分钟**（镜像复制 + 新建实例 + 改 DNS + DB 同步一次）。

### 3.5 域名规划（一个域名能放多少站点/页面 · 2026-09-11）

**结论**：一个域名可承载**不限数量**的页面与服务，限制不在域名本身，而在「怎么把请求路由到不同后端」。

四种区分方式（由推荐到不推荐）：

| 方式 | 示例 | 数量 | 说明 |
|---|---|---|---|
| **同域名下按路径** | `voc.example.com/compare`、`/api/*` | 无限 | 本项目 VPS 版即此形态：一个 uvicorn 同时托管 SPA 5 页 + 全部 API + Agent 抽屉（hash 路由 `#/compare` 不进服务端） |
| **子域名** | `voc.example.com`（VPS）、`snapshot.example.com`（EdgeOne 静态站） | 理论无限 | 区分「不同站点」最干净的方式；DNS 各加一条记录即可 |
| **端口** | `voc.example.com:8443` | 受端口限制 | 仅用于临时/内测（如备案期自测）；浏览器默认只走 80/443，不友好 |
| **多 IP** | 同一域名解析到多台机 | 不限 | 属负载均衡，**不能按路径分流**；按路径/Host 分流必须靠反向代理（Caddy/Nginx） |

**本项目推荐映射**（与 §0.5 架构对应）：

```
voc.example.com        → VPS（Caddy → uvicorn:8000）：实时查询 + Agent 对话 + admin
snapshot.example.com   → EdgeOne Pages（CNAME 到平台分配域名）：静态快照门面，永不宕机
```

**五条容易踩的配套约束**：

1. **HTTPS 证书**：Caddy 对每个域名自动申请；子域多时可用一张通配符 `*.example.com`（DNS-01 挑战）。通配符只覆盖一级子域（不含 `a.b.example.com`）。
2. **CORS / 同源**：不同子域 = 不同源。若把静态前端放 `static.` 而后端 API 在 `voc.`，跨源调用需配 CORS——**当前方案不涉及**（前端由 uvicorn 同源托管）。
3. **Cookie 域**：admin session 若要跨子域共享需设 `domain=.example.com`；不需要就保持默认（更安全）。
4. **备案（大陆节点）**：ICP 备案按**主域名**登记网站，其子域名一般随主域名一并可用，无需逐个备案；换接入商时做「接入备案」。香港节点无此约束。
5. **别让同一主机名同时指向两处**：主域解析到 VPS、静态站用子域——不要用 A 记录把同一主机名指向两台不同用途的机器（会随机分流，证书与业务都会乱）。

### 3.6 备案操作顺序与时间线（2026-09-11 · 已购大陆域名）

**结论：先买轻量服务器，再走备案**（顺序不能反）。

两个硬性前置（官方文档核实）：

1. **备案必须挂在一个已购买的大陆境内云资源上**——腾讯云轻量文档明确：需先购买**中国内地地域**轻量实例，**包年包月**且**购买时长 > 3 个月**；备案服务码从该实例申请（买 1 年即满足）。
2. **域名实名认证后需等 3 个自然日**（非腾讯云注册的域名需满 3 个工作日）才能提交备案，且**实名信息必须与备案主体一致**。

**最优时间线（部署与备案并行，不互相等）**

| 时间 | 做什么 | 依赖 |
|---|---|---|
| Day 0 | 买大陆轻量（包年包月 ≥3 个月，建议 1 年）→ 拿到 IP | — |
| Day 0 | **不等备案**：按 §4 步骤 1–5 部署，用 `http://<IP>:8443` 自测（非标端口不受未备案阻断） | 有 IP |
| Day 0 | 确认域名实名认证已通过（注册商处） | 域名已买 |
| Day 0–3 | 等实名数据同步工信部（3 个自然日） | 实名通过 |
| Day 3+ | 轻量控制台申请**备案服务码** → 提交**首次 ICP 备案**（主体 + 网站 + 域名） | 实例 + 同步完成 |
| Day 4–6 | 腾讯云初审（1–2 个工作日） | 提交完成 |
| Day 6–20 | 通信管理局审核（一般 1–2 周，最长 20 工作日） | 初审通过 |
| 通过后 | DNS A 记录指向 VPS → Caddy 自动申请 HTTPS → 域名正式上线 | 备案号下发 |
| 通过后 30 日内 | 完成**公安联网备案**（全国互联网安全管理服务平台） | ICP 备案号 |

> ⚠️ 备案期间**不要让域名对外提供 web 服务**（未备案的域名解析到大陆 IP 会被拦），但用 `IP:8443` 自测完全没问题——这正是「部署先行、域名后切」的价值。

**备案常见坑（提前规避）**

1. **网站名称/性质**：个人备案不能写「平台 / 论坛 / 官网」等词，建议写「个人技术学习笔记 / 数据分析学习记录」这类中性名称；避免经营性表述。
2. **交互式服务风险**：本项目含 Agent 对话（用户输入）与留言式交互，个人备案审核可能关注这一点——**内测阶段建议限制访问**（admin 口令 / 白名单 / 不公开分享），对外描述保持「数据分析展示」。
3. **域名后缀**：需为工信部批复后缀（`.cn` `.com` `.net` `.top` 等可以；部分新后缀不可备案）——购买前确认。
4. **主体一致性**：域名持有者、备案主体、网站负责人三者信息必须一致（个人备案 = 身份证信息），否则驳回。
5. **接入商**：必须与实际托管服务商一致（我们就是腾讯云，一致）。

---

## 4. 部署步骤（顺序不可乱）

### 步骤 1 · VPS 初始化（5 分钟）

```bash
# 用 Oracle Cloud 控制台或 SSH 密钥登录（首次用密码登录会强制改密钥）
ssh ubuntu@<VPS_IP>

# 1.1 创建非 root 用户 voc（所有应用都以它运行）
sudo adduser voc
sudo usermod -aG sudo voc

# 1.2 切换到 voc，把本机公钥加入 voc 的 authorized_keys
sudo mkdir -p /home/voc/.ssh
sudo cp ~/.ssh/authorized_keys /home/voc/.ssh/   # 如果你用 ubuntu 用户登录过
sudo chown -R voc:voc /home/voc/.ssh
sudo chmod 700 /home/voc/.ssh
sudo chmod 600 /home/voc/.ssh/authorized_keys

# 1.3 关密码登录 + 改 SSH 端口（强烈推荐）
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
# 可选：改端口减少扫描
sudo sed -i 's/^#\?Port .*/Port 22222/' /etc/ssh/sshd_config
sudo systemctl restart sshd

# 1.4 系统更新
sudo apt update && sudo apt upgrade -y
```

### 步骤 2 · 安装基础环境（5 分钟）

```bash
sudo apt install -y python3.11 python3.11-venv python3-pip \
                    sqlite3 git curl ufw fail2ban \
                    caddy

# 验证 Caddy
caddy version

# 启用 ufw 防火墙（关键安全防线）
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22222/tcp   # SSH 自定义端口，与上面保持一致
sudo ufw allow 80/tcp      # HTTP（仅供 Caddy 自动跳转 + ACME 验证）
sudo ufw allow 443/tcp     # HTTPS
sudo ufw enable
sudo ufw status verbose
```

### 步骤 3 · 部署代码（3 分钟）

```bash
# 切换到 voc 用户
sudo -iu voc

# 3.1 拉取私有仓库（提示输入用户名 + PAT；PAT 可设 1 天有效，用完 revoke）
cd ~
git clone https://github.com/<你的用户名>/voc-platform.git
cd voc-platform

# 3.2 创建项目虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# 3.3 装核心 + 仪表盘依赖（**不要装 ML**！VPS 上没 GPU 且没必要）
pip install -r requirements-dashboard.txt

# 3.4 验证
python scripts/smoke_test.py
```

### 步骤 4 · 配置 secrets（5 分钟）

```bash
# 4.1 创建 .env（权限 600）
cp .env.example .env
chmod 600 .env
nano .env
# 填入真实值：STEAM_API_KEY / DEEPSEEK_API_KEY / ANALYZER_PROVIDER=deepseek
# DATABASE_URL 保持默认 sqlite:///data/voc.db
# BILIBILI_SESSDATA 可选

# 4.2 创建 data 目录（DB 落点）
mkdir -p data logs backups
chmod 700 data logs backups

# 4.3 首次采一次（生成 DB）
python scripts/ops/daily_incremental_collect.py --no-download --no-upload
ls -la data/voc.db   # 应看到文件，权限自动继承 voc:voc
```

### 步骤 5 · 配置 cron 任务（5 分钟）

```bash
# 编辑 voc 用户的 crontab
crontab -e

# 加入以下 3 行
# 注：本项目 GH Actions `collect` job 已停用（数据链路切本地直采），仅保留 `test` job 作 CI 护栏；
# VPS 自托管每日 02:00（北京时间）跑采集，03:00 跑补采哨兵检查 02:00 是否成功。
# 1. 每日采集 + 标注（北京时间 02:00）
0 18 * * * cd /home/voc/voc-platform && /home/voc/voc-platform/.venv/bin/python scripts/ops/daily_incremental_collect.py --no-download --no-upload >> /home/voc/voc-platform/logs/cron.log 2>&1

# 1.5 每日采集哨兵（北京时间 03:00）：检查 02:00 任务是否失败/未跑，是则补采
0 19 * * * cd /home/voc/voc-platform && /home/voc/voc-platform/.venv/bin/python scripts/ops/check_daily_collect.py >> /home/voc/voc-platform/logs/collect-check.log 2>&1

# 2. 每周日 03:00 备份 DB（保留最近 5 份）+ VACUUM（先 checkpoint WAL 再 vacuum）
#    注：2026-09-02 起 DB 运行在 WAL 模式（Web 看板读写并发），备份前先 PRAGMA wal_checkpoint(TRUNCATE)
#    确保 -wal 文件合并进主库再 cp，否则备份可能缺最近数据。
0 3 * * 0 cd /home/voc/voc-platform && sqlite3 data/voc.db 'PRAGMA wal_checkpoint(TRUNCATE);' && cp data/voc.db backups/voc-$(date +\%Y\%m\%d).db && ls -1t backups/voc-*.db | tail -n +6 | xargs -r rm && sqlite3 data/voc.db 'VACUUM;'

# 3. 每 10 分钟健康检查（DB 至少能被 Streamlit 打开）
*/10 * * * * /home/voc/voc-platform/.venv/bin/python -c "from src.storage.db import init_db; _, S = init_db(); s = S(); print(f'[{s.execute(\"select count(*) from comments\").scalar()}] OK')" >> /home/voc/voc-platform/logs/health.log 2>&1
```

> **关键**：cron 用 `voc` 用户的 crontab（**不要**用 `/etc/crontab` 或 root），保证 DB 文件权限不被污染

### 步骤 6 · Streamlit systemd 服务（5 分钟）

```bash
# 6.1 创建 systemd unit（仍以 voc 用户身份）
sudo tee /etc/systemd/system/voc-streamlit.service <<'EOF'
[Unit]
Description=VoC Streamlit Dashboard
After=network.target

[Service]
Type=simple
User=voc
Group=voc
WorkingDirectory=/home/voc/voc-platform
Environment="PATH=/home/voc/voc-platform/.venv/bin:/usr/bin"
Environment="VOC_SKIP_EMBEDDING=1"
ExecStart=/home/voc/voc-platform/.venv/bin/streamlit run app.py \
    --server.address=127.0.0.1 \
    --server.port=8501 \
    --server.headless=true \
    --browser.gatherUsageStats=false
Restart=always
RestartSec=5
StandardOutput=append:/home/voc/voc-platform/logs/streamlit.log
StandardError=append:/home/voc/voc-platform/logs/streamlit.log

# 安全加固
# ⚠️ ProtectHome 只能用 read-only：=true 会把 /home 挂成空目录，venv 里的
#    streamlit 二进制无法解析 → systemd 报 status=203/EXEC 起不来（2026-09-11 实测）
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/voc/voc-platform/data /home/voc/voc-platform/logs

[Install]
WantedBy=multi-user.target
EOF

# 6.2 启动 + 开机自启
sudo systemctl daemon-reload
sudo systemctl enable --now voc-streamlit

# 6.3 验证
sudo systemctl status voc-streamlit
curl -I http://127.0.0.1:8501   # 应返回 200 / 303
```

### 步骤 6.5 · Web 看板 FastAPI 服务（可选，2026-09-02）

```bash
# 6.5.1 依赖（fastapi/uvicorn 已随 requirements-dashboard.txt；如未装则）
sudo -iu voc
cd ~/voc-platform && source .venv/bin/activate
pip install -r requirements-dashboard.txt

# 6.5.2 .env 追加两行（生成方式见 §3.1）
#   ADMIN_PASSWORD_HASH=pbkdf2_sha256$240000$...
#   SESSION_SECRET_KEY=<random hex>
#   公网 HTTPS 下建议 COOKIE_SECURE=1
chmod 600 .env

# 6.5.3 systemd unit（与 §6 Streamlit 服务并列；二选一或并存均可）
sudo tee /etc/systemd/system/voc-web.service <<'EOF'
[Unit]
Description=VoC Web Dashboard (FastAPI)
After=network.target

[Service]
Type=simple
User=voc
Group=voc
WorkingDirectory=/home/voc/voc-platform
Environment="PATH=/home/voc/voc-platform/.venv/bin:/usr/bin"
ExecStart=/home/voc/voc-platform/.venv/bin/uvicorn src.api.main:app \
    --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
StandardOutput=append:/home/voc/voc-platform/logs/web.log
StandardError=append:/home/voc/voc-platform/logs/web.log

# 安全加固（同 §6.1）
# ⚠️ ProtectHome 只能用 read-only：=true → /home 空目录 → 203/EXEC（2026-09-11 实测）
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/voc/voc-platform/data /home/voc/voc-platform/logs

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now voc-web
curl -I http://127.0.0.1:8000/api/health   # 应 200 {"ok":true,"comments":N}
```

> Caddy 反代目标由 `8501`（Streamlit）改为 `8000`（Web 看板）即可——二选一，或继续并存各自绑端口。

### 步骤 7 · Caddy 反向代理（内测 8443 → 域名 HTTPS）

> **两个阶段**：备案未通过前用 `:8443` + **自签 TLS**（2026-09-11 起，`default_sni` 必需）、IP 直连自测；备案通过 + DNS 解析就绪后换成域名块，Caddy 自动签发真证书。

**7A · 内测阶段（已落地，2026-09-11）**

> ⚠️ **指令名坑（2026-09-11 实测）**：Caddy **2.6.2（apt/universe 源）里叫 `basicauth`**，`basic_auth` 是 **2.8+ 才有的新名**。照 2.8+ 文档写 `basic_auth` 会 `validate` 失败并报
> `unrecognized directive: basic_auth`（本站首次部署即踩此坑）。**本手册以下片段统一用 `basicauth`**。

> **外发前先加 P0-1 全站准入**（`basicauth`）。basic_auth 只是 base64 编码——**挡得住扫描器/爬虫，挡不住中间人嗅探**；所以准入**必须**与 TLS 同时具备（2026-09-11 起本站已是 `https://`，见 §5.5.4 / 下方 `tls internal`）。

```bash
# ① 生成口令哈希（每人一个账号；对每个内测人员各跑一次，交互输入明文）
sudo caddy hash-password            # 输出形如 $2a$14$xxxxxxxx...
#  非交互（注意 shell history 会留痕，生成后清 history）：
#  caddy hash-password --plaintext '一行随机口令'

# ② Caddy 由 apt 安装（Ubuntu 24.04 universe 源，2.6.2）；日志目录需先建好
sudo mkdir -p /var/log/caddy && sudo chown caddy:caddy /var/log/caddy

# 改配置前先备份（tee 会原地覆盖；写坏了靠它回滚）
sudo cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-$(date +%Y%m%d-%H%M%S)

# 更稳的姿势：先 tee 到 /tmp/Caddyfile.new → validate 通过再 cp 覆盖 →
# 这样"配置写错"永远不会碰到线上文件（本站 2026-09-11 即用此法，一次写错被完美挡下）
sudo tee /etc/caddy/Caddyfile <<'EOF'
{
    # 【必须有】客户端用 IP 字面量访问时**不发 SNI**（RFC 6066 禁止把 IP 当 SNI），
    # Caddy 会因此选不到证书，回 TLS alert internal error(80)，浏览器/curl 全部连不上。
    # default_sni 在 SNI 为空时补上本机 IP，使内部 CA 签出的（含 IP SAN 的）证书能被选中。
    # 2026-09-11 实测：不加这一行 → 不带 -servername 的 openssl 失败、curl 000；加后正常。
    default_sni <本机IP>
}

https://<本机IP>:8443 {
    tls internal        # 内部 CA 自签（P0-A）：加密强度等同 CA 证书，只是"没花钱买信任"

    encode gzip zstd

    # P0-5（2026-09-11）：单请求体积上限，挡超大 JSON 直灌 LLM / 写库
    # （SPA 正常请求为几十 KB 级，1MB 有充裕余量；应用层另有逐字段长度校验）
    request_body {
        max_size 1MB
    }

    # P0-1（2026-09-11）：全站准入，覆盖 SPA + 全部 API。每行一个内测人员。
    # 用 caddy hash-password 生成的 bcrypt 哈希替换下方占位；不要写明文口令。
    # 指令名：Caddy 2.6.2 => basicauth；2.8+ => basic_auth（写错直接 validate 失败）
    basicauth {
        tester01 $2a$14$REPLACE_WITH_HASH_1
        tester02 $2a$14$REPLACE_WITH_HASH_2
    }

    reverse_proxy 127.0.0.1:8000 {
        # P0-2（2026-09-11）：覆盖 X-Forwarded-For，丢弃客户端伪造值。
        # 默认行为是"追加"，客户端可自带 XFF 绕过限流；显式覆盖后应用层拿到的就是真实 IP。
        # ⚠️ `caddy validate` 会就此行报 "Unnecessary header_up X-Forwarded-For" 警告，是误报，别删。
        header_up X-Forwarded-For {remote_host}
    }

    header {
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"

        # P1-4（2026-09-11）：CSP + Permissions-Policy。白名单来自实测清点，全站外部来源只有 2 处：
        #   img  → Steam CDN 封面兜底链（compare.js）；frame → B站播放器 iframe（bilibili.js）
        # script-src 为纯 'self'：唯一的内联事件处理器（<img onerror>）已改为 document 捕获监听；
        #   vendor 里 ECharts 仅一处 new Function，在 JSON.parse 兜底分支（现代浏览器永不执行）。
        # style-src 保留 'unsafe-inline'：ECharts 与页面模板大量内联样式，去掉会整片白屏。
        Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://cdn.cloudflare.steamstatic.com; font-src 'self' data:; connect-src 'self'; frame-src https://player.bilibili.com; media-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=()"

        # 刻意不加 Strict-Transport-Security：IP 字面量 + 自签的组合下，若浏览器记住 HSTS，
        # 证书警告会变成"不可绕过"，测试者被锁在门外。留到 §7B 换真证书时再加。
        -Server
    }

    log {
        output file /var/log/caddy/voc.log {
            # P1-3（2026-09-11）：原先无任何轮转 → 单文件无限增长（每行含完整请求头，约 1KB）
            roll_size 20MiB
            roll_keep 5
            roll_keep_for 30d
        }
    }
}
EOF

sudo caddy validate --config /etc/caddy/Caddyfile   # 应打印 Valid configuration
sudo systemctl reload caddy                         # reload 平滑；restart 亦可

# ③ 验证准入生效（关键）：不带凭据必须 401，带对凭据才 200
#    注意 curl 对 IP 字面量**不发 SNI**，正好等价于"最坏客户端"，必须用它做验收
curl -sk -o /dev/null -w 'no-auth=%{http_code}\n' https://<本机IP>:8443/api/health          # 期望 401
curl -sk -u tester01:'<明文口令>' https://<本机IP>:8443/api/health                          # 期望 {"ok":true,...}
```

> ⚠️ 云厂商控制台（腾讯云轻量**防火墙**）也要放通 8443/TCP，否则本机 `curl` 通、公网仍打不通。
> ⚠️ `basicauth` 是全站生效的：浏览器首次访问会弹原生登录框；对内测人员说明「用分配的账号密码」即可。撤销某人只需从 `basicauth` 块删掉那一行并 `systemctl reload caddy`。
> 🚨 **改配置的守卫（2026-09-11 实战教训）**：`/tmp` 是共享的，本站曾因把**上一轮会话遗留的 `/tmp/Caddyfile.new`**（内容是同名但**旧口令哈希**的版本）当成自己的候选文件 `install` 上去，导致 gate 口令被静默回退。现在固定两道守卫：①候选文件用**唯一文件名**（如 `/tmp/p0a_Caddyfile.new`）并校验 `stat -c %U` 属主；②安装前 `diff` 出 `basicauth` 行的哈希与**线上现网值一字不差**，不一致直接中止。
> 🚨 **行尾**：Windows 端 `write_to_file` 产出的是 CRLF，`sed -i 's/\r$//'` 归一化后再比哈希/装盘 —— 否则 `$2a$14$…\r` 与线上的 `…` 判为不等，守卫会误拦（本站踩过）。

**7A-1 · 实测记录（2026-09-11，照本篇执行的线上结果）**

| 验证项 | 实测结果 |
|---|---|
| `basicauth` 准入 | 无凭据 `GET /api/health` → **401**；错口令 → **401**；正确凭据 → **200** + `{"ok":true,"comments":18916}`；SPA `/` → **200** |
| 公网真实路径 | `http://134.175.115.248:8443/api/health` → 401（无凭据）/ 200（有凭据）；首页 200 |
| P0-2 + P0-3 | 连打 `/api/targets` **130 次**，每次换一个伪造 `X-Forwarded-For: 9.9.9.N` → `200=120 / 429=10`，**第 121 次开始 429** ⇒ 伪造 XFF 不产生新桶，Caddy 覆盖 XFF 与应用层取末段**同时**生效 |
| P0-5（应用层） | 200KB 报文（过 Caddy）→ **422**，被 `ChatBody.user_msg` 长度上限拦下 |
| P0-5（传输层） | 2MB 报文 → **502**（原因见下）；Caddy 日志 `"status":502,"size":0,duration≈3ms`，**应用 `web.log` 里查不到这条请求** ⇒ 报文根本没进应用 |
| P0-4（SSE） | 建会话后 `POST /api/agent/chat`：`ttfb≈1.2s / total≈2.6s`，收到 `event: token` + `event: done`，会话落库 `['user','assistant']` ⇒ **并发闸没有阻塞流式** |
| fail2ban | `sshd` + 新增 `voc-caddy-401` 两个 jail 均 active（见 §7A-2） |

> ⚠️ **2MB 超限回 502（而非 413）是预期行为，不是故障**：Caddy 的 `request_body` 处理器会在**流式转发请求体**时发现超限，该错误由 `reverse_proxy` 上报，于是被映射成 `502 Bad Gateway`（3ms 内返回、`size:0`、上游无感知）。
> 安全效果等价——超大报文**进不了应用**，内存与 LLM 成本都守住了；只是状态码语义不精确（浏览器/`curl` 看到 502 不会误判为成功）。
> **不建议**改回 413：那需要在 `reverse_proxy` 之前用 `expression` 匹配 `Content-Length`，而 **chunked 请求（无 `Content-Length`）会绕过它**，反而更不安全。`request_body` 能兜住 chunked，故保留。

**7A-2 · 给 Caddy 加 fail2ban jail（防 `basicauth` 爆破）**

```bash
sudo tee /etc/fail2ban/filter.d/caddy-voc.conf <<'EOF'
[Definition]
# Caddy JSON 访问日志：basic auth 失败记为 status 401
failregex = ^.*"remote_ip":"<HOST>".*"status":401.*$
            ^.*"status":401.*"remote_ip":"<HOST>".*$
ignoreregex =
EOF

sudo tee -a /etc/fail2ban/jail.local <<'EOF'

[voc-caddy-401]
enabled  = true
port     = 8443
filter   = caddy-voc
logpath  = /var/log/caddy/voc.log
maxretry = 20
findtime = 10m
bantime  = 1h
# 回环豁免：本机 curl 自测会产生 401，先把自己豁免掉；给自己固定出口 IP 也建议加进来
ignoreip = 127.0.0.1/8 ::1
EOF

sudo fail2ban-client -t          # 先做配置测试，OK 再重启（失败则线上不受影响）
sudo systemctl restart fail2ban
sudo fail2ban-client status voc-caddy-401
```

> 撤销封禁：`sudo fail2ban-client set voc-caddy-401 unbanip <IP>`；查已封：`sudo fail2ban-client status voc-caddy-401`。

**7A-3 · 实测记录（2026-09-11 · P0-A 自签 TLS + P1 收口）**

| 验证项 | 实测结果 |
|---|---|
| 证书 | `issuer=CN = Caddy Local Authority - ECC Intermediate`；`SAN: IP Address:134.175.115.248`；有效期 12h（Caddy 内部 CA 默认短周期，到期自动续签） |
| **无 SNI 客户端** | 加 `default_sni` **前**：不带 `-servername` 的 `openssl s_client` → `tlsv1 alert internal error(80)`、`curl` → `000`；**加后 `curl` 第一次探测即 `200`** |
| 准入未回退 | 无凭据 → **401**；错口令 → **401**；正确凭据 → **200** |
| 明文口已关闭 | `http://<IP>:8443/` → **400**（`Client sent an HTTP request to an HTTPS server`） |
| 公网可达 | 本机（等价测试者机器）`curl -k https://134.175.115.248:8443/api/health` → `{"ok":true,"comments":18916}` |
| **SSE 未被 h2 缓冲** | 经 Caddy TLS（协商到 **HTTP/2**）`POST /api/agent/chat` → `200`，正常吐 `event: token` + `event: done` 且内容完整；审计行照常落库 |
| P1-2 文档开关 | `/docs`、`/redoc`、`/openapi.json` → **404**（前一轮为 200）；SPA `/`、`/api/targets`、`/api/overview` 仍 **200** |
| 安全响应头 | 5 项全命中：CSP / Permissions-Policy / X-Content-Type-Options / X-Frame-Options / Referrer-Policy |
| Secure cookie | `POST /api/auth/login` → `HTTP/2 200` + `set-cookie: session=…; httponly; samesite=lax; secure`（`COOKIE_SECURE=1` 生效） |
| ufw | 收口为 **22 + 8443**（v4/v6 各一条），SSH 仍可连 |
| 日志轮转 | `roll_size 20MiB` / `roll_keep 5` / `roll_keep_for 30d` 已生效（当前 1.3 MB，未触发切分） |
| fail2ban | 升级 openssh 后仍 `sshd` + `voc-caddy-401` 两 jail active |
| 系统补丁 | 172 → **5** 可升级（余下为内核/`linux-firmware`/`fwupd`，需 `dist-upgrade` + 重启）；`caddy` 仍 2.6.2、`/etc/caddy/Caddyfile` **未被包内 conffile 覆盖** |
| 启动自检 | `voc-web` active 且日志**无** `COOKIE_SECURE≠1` 告警 ⇒ `PUBLIC_MODE=1` 自检通过 |
| 服务开机自启 | `voc-web` / `caddy` / `fail2ban` / `ssh` / `ufw` / `unattended-upgrades` 全部 active + enabled |

> 回滚点（均在 `/root/`）：`Caddyfile.bak-<ts>`（P0-A 各步）、`Caddyfile.preupgrade-<ts>`、`env.preupgrade-<ts>`、`app_rollback_<ts>/{env,main.py,index.html,compare.js}`。
> 「误装陈旧 `/tmp` 文件」事件处置：`diff` 确认唯一差异是 `basicauth` 两行哈希（= 旧口令）→ 立刻用备份 `install` 还原（md5 与备份一致）→ 新口令验证 `200`、错口令验证 `401`，确认 gate 未被回退。
> **Caddy 日志不泄露 Basic Auth 口令**：Caddy 默认把 `Authorization` / `Cookie` 头的**值打码成 `[]`**（只留 key 名）。本站实测 `grep -o '"Authorization":\[[^]]*\]'` 取到 `[]`、解码为空 ⇒ 日志外传不泄口令。但 Caddy **会**记 basic-auth **用户名** —— 这正是「一人一账号」能溯源的原因。

**7B · 域名阶段（备案通过后）**

```bash
sudo tee /etc/caddy/Caddyfile <<'EOF'
erself.site {
    encode gzip zstd
    request_body { max_size 1MB }             # P0-5：单请求体积上限
    # ⚠️ 切域名阶段**不要把准入一起丢掉**：P0-1 的 basicauth 必须一并搬过来，
    #    否则 HTTPS 只解决了"明文可被嗅探"，站点又变回对全网裸奔（§5.5.1 要求 SPA 层有准入）。
    #    哈希沿用 §7A 生成的即可（同一哈希可跨配置复用）；2.6.2 用 basicauth。
    basicauth {
        tester01 $2a$14$REPLACE_WITH_HASH_1
        tester02 $2a$14$REPLACE_WITH_HASH_2
    }
    reverse_proxy 127.0.0.1:8000 {
        header_up X-Forwarded-For {remote_host}   # P0-2：覆盖伪造 XFF
    }
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }
    log {
        output file /var/log/caddy/voc.log
    }
}
EOF

# DNS 加 A 记录 erself.site → 134.175.115.248；确认 80/443 已放通
sudo systemctl reload caddy
curl -I https://erself.site     # 应 200 + HSTS（证书自动申请）
```

### 步骤 8 · 验收（5 分钟）

```bash
# 8.1 公网访问：浏览器打开 https://voc.example.com
# 8.2 检查 cron 跑过一次：查看 logs/cron.log
tail -50 logs/cron.log

# 8.3 确认 DB 没暴露在公网（关键安全检查！）
curl https://voc.example.com/data/voc.db    # 应 404 / 连接被拒
curl https://voc.example.com/.env           # 应 404
ls -la data/voc.db                          # 应 -rw------- voc voc

# 8.4 关停 GitHub Actions cron（避免重复跑造成数据冲突）
# （2026-09-11 起）GitHub Actions 里已无采集 workflow —— 原 daily-collect.yml / bilibili-daily.yml
# 已删除，仓库只剩 ci.yml（pytest 门禁）。原先「关掉云端采集」这一步已无需执行；
# 若要连 CI 也停掉：Settings → Actions → General → Disable
# 或删除 .github/workflows/ 下的两个文件（推荐）
```

---

## 5. 安全加固清单（必查）

> **7 项最低红线**（部署完逐项确认）
>
> - [ ] **DB 文件权限 600**：`ls -la data/voc.db` 显示 `-rw------- voc voc`（其他用户无任何权限）
> - [ ] **.env 权限 600**：`ls -la .env` 同样
> - [ ] **Streamlit 监听 127.0.0.1**：`ss -tlnp | grep 8501` 显示 `127.0.0.1:8501`，**不是** `0.0.0.0`
> - [ ] **SSH 仅密钥登录**：`PasswordAuthentication no`
> - [ ] **SSH 改端口或限制来源**：`/etc/ssh/sshd_config.d/` 加 `AllowUsers voc@<你的IP段>`
> - [ ] **防火墙只开 80/443/SSH**：`sudo ufw status` 验证
> - [ ] **fail2ban 启用**：`sudo systemctl enable --now fail2ban`（自动封禁 SSH 爆破 IP）
>
> **进阶（可选但强烈推荐）**
>
> - [ ] **Cloudflare 反代**：域名走 Cloudflare 代理，隐藏 VPS 真实 IP
> - [ ] **SSH 端口敲门**：用 `knockd` 或 `ufw` 的 `limit` 限制 SSH 尝试频率
> - [ ] **Streamlit 鉴权**：在 `app.py` 顶部加 `st.experimental_user` 或 `st.auth`（Streamlit 1.30+）
> - [ ] **DB 加密备份**：用 `age` 或 `gpg` 加密 `backups/voc-*.db` 后再推到对象存储
> - [ ] **告警脚本**：`scripts/ops/healthcheck.sh` 配 cron + Telegram / 邮件告警

---

## 5.5 内测期安全加固（备案前 · 2026-09-11）

> **背景**：备案未通过前只能用 `http://<IP>:8443` 明文 + 非标端口自测，但 IP 一旦对外分发就会被扫描器收录。本节是「把链接发给内测人员之前必须做完」的清单——**不修完不要外发**。
>
> **关联**：代码整改见 `src/api/auth.py`（限流 / IP 取信）、`src/api/main.py`（启动自检）；Caddy 配置见 §7A。

### 5.5.1 暴露面盘点（整改前）

| 层级 | 端点 | 整改前防护 | 整改后 |
|---|---|---|---|
| SPA | `/`（5 页看板） | 无 | Caddy `basicauth`（已上线） |
| 公开只读 API | `/api/targets` `/overview` `/topics` `/comments` `/opinions` `/compare` `/trends` `/wordcloud` `/bilibili/videos` `/danmaku` `/games/meta` | **零限流** | 120 req/min/IP |
| Agent API | `/api/agent/sessions`(CRUD) `/chat`(SSE) `/search` `/export` | 60 req/min/IP（**XFF 可伪造绕过**） | 30 req/min/IP + 日额度 + 并发上限 |
| 管理 API | `/api/admin/*` | admin session（fail-closed） | 不变 |
| 传输 | 8443 | **明文 HTTP** | 备案后 §7B 上 TLS |

### 5.5.2 P0 必做清单（逐项勾选，缺一不外发）

- [x] **P0-1 全站准入**：Caddy `basicauth`（bcrypt，**2.6.2 的指令名**；2.8+ 才叫 `basic_auth`），覆盖全站含 API。**建议每人一个账号**——否则口令泄漏后无法定位、只能全员封禁。配置见 §7A，实测见 §7A-1。
- [x] **P0-2 修复 XFF 伪造绕过限流**：`auth.client_ip()` 原取 `X-Forwarded-For` **首段**，而 Caddy 反代把真实 IP 追加在**末尾**且不覆盖客户端自带值 → 攻击者自带伪造头即可每次换 IP，绕过全部限流。修复：①Caddy `header_up X-Forwarded-For {remote_host}`（覆盖，丢弃伪造值）；②应用层取**末段**。
- [x] **P0-3 公开只读端点限流**：`/api/wordcloud` 是 jieba + 跨游戏 TF-IDF **重算**、`/compare`/`/trends` 亦为 CPU 密集，2C2G 单机一个循环脚本即可打满。整改：通用 `Depends` 限流器挂 `public_router`（一处生效，覆盖全部公开只读端点），阈值分级（读端点 `PUBLIC_RATE_LIMIT_PER_MIN`，默认 120；Agent `AGENT_RATE_LIMIT_PER_MIN`，默认 30）。`/api/health` 挂在 app 上不受影响（供监控探活）。
- [x] **P0-4 Agent 成本熔断**（2026-09-11 上线，SSE 实测不阻塞流式）：原仅 per-IP 计数，无全局日额度、无 `max_tokens`、无并发上限。`chat.py` 单次最多 5 轮 tool，每轮全量 messages 重发（token 近 O(轮数²)）→ 一天可烧干余额。整改（均已落地于 `src/api/auth.py` + `src/agent/chat.py`）：
  - `AGENT_DAILY_CHAT_LIMIT`（默认 300）+ `AGENT_DAILY_CHAT_LIMIT_PER_IP`（默认 50）——自然日（UTC+8）额度，超限 429；额度在**会话归属校验之后**扣减，避免被越权请求刷爆。
  - `AGENT_MAX_TOKENS`（默认 2048）——单轮输出上限，直接传给 LLM。
  - `AGENT_MAX_CONCURRENCY`（默认 2）+ `AGENT_QUEUE_WAIT_SEC`（默认 20）——全局并发闸，满时短时排队、超时发 `error(busy)` 事件（SSE 已开始，无法再返 429）。
  - ⚠️ 计数在进程内（uvicorn 单 worker），**重启即清零**；持久化随 P1 审计日志一起做。
- [x] **P0-5 请求体大小限制**（2026-09-11 上线，实测 200KB→422 / 2MB→502）：`ChatBody.user_msg` / `history` 原无长度上限，可塞超大 JSON 直灌 LLM（按 token 计费）或写库。整改（已落地 `src/api/routers_agent.py`）：
  - `user_msg ≤ 4000`、`history ≤ 50 条`、每条 `content ≤ 20000`；`session_id ≤ 64`、`tool_call_id ≤ 128`、`tool_name ≤ 64`；`CreateSessionBody` 的 `page_context ≤ 8000` / `title ≤ 100` / `model ≤ 64`（超限一律 422）。
  - Caddy `request_body { max_size 1MB }`（§7A / §7B）——传输层兜底。

### 5.5.3 P1 建议（内测期 · 2026-09-11 代码落地 3/4）

- [x] **审计日志**（`src/api/access_log.py`）：内测是观察期，不知道谁在用就没法定阈值、出事后无法溯源。实现与原计划的**两处有意偏离**：
  - **单独 DB 文件** `data/access_log.db`（env `ACCESS_LOG_DB`）而非主库建表 —— 主库每天 02:00 被 `push_db_to_vps.ps1` 整库覆盖到 VPS，审计表放主库**每天被清空**、且每晚 scp 多带这些行。
  - **列为 `ts / ip / method / path / status / anon / duration_ms`**（原计划 5 列）——多 `method` 以区分 SSE 对话与同路径其它动词，多 `duration_ms` 回答"谁在被刷"。**不记 query string / body**（`/api/agent/chat` 的 body 是提问原文，属体验数据非审计必需）。
  - 请求路径只写内存 deque，后台每 `ACCESS_LOG_FLUSH_SEC`（默认 5s）批量落库，**落库失败即丢弃**（观测设施不得拖垮业务）；保留 `ACCESS_LOG_RETENTION_DAYS`（默认 30 天）；静态资源与 `/api/health` 不入库。中间件为**纯 ASGI**（不用 `BaseHTTPMiddleware`），避免给 SSE 流多套一层转发。
  - ⚠️ Caddy `basicauth` 拒绝的 401 **不经过应用** → 该部分由 fail2ban 读 Caddy JSON 日志覆盖（§7A-2）。
- [x] **`PUBLIC_MODE=1` 启动自检**（`src/api/main.py::_check_public_mode`）：**fail-closed** —— 缺 `SESSION_SECRET_KEY` / `ADMIN_PASSWORD_HASH`、`AGENT_DAILY_CHAT_LIMIT`(或 `_PER_IP`) ≤ 0、或未声明 `ENTRY_AUTH_ENFORCED=1` 时**拒绝启动**（systemd 反复重启 + 日志一句人话，胜过"看着在跑其实全裸"）。仅告警不拦截：`COOKIE_SECURE`（备案前是明文 `:8443`，强设会让 admin 登不上）、`ACCESS_LOG_ENABLED`（关掉只少观测）。
- [x] **B 站评论脱敏**（`src/api/service.py::_public_comment`，公开端点与 Agent 工具共同经过）：B 站 `author` 是**昵称**、`extra.profile.uname`/`official` 属个人信息。处理：`author` → 稳定伪名（昵称 sha1 前 8 位，同账号跨页可辨认但无法反查）、`profile` 删 `uname`/`official` 保留 `level`/`vip`/`sex`。库里**仍留原文**（本机离线分析要用），脱敏只发生在对外序列化这一层；Steam 侧只落 steamid（匿名 ID）保持不动。`Comment.to_dict()` 不含 `author_id`(mid)，故对外不暴露用户空间链接。
- [x] **CSP / Permissions-Policy**（Caddy 头，§7A）—— **2026-09-11 落地**（实测见 §7A-3）。要点：`script-src` 已是**纯 `'self'`**（无 `'unsafe-inline'`/`'unsafe-eval'`）——为此刻意去掉前端唯一的内联事件处理器（`compare.js` 的 `<img onerror=…>` 改为 `document` 级**捕获**监听，因 `error` 不冒泡但捕获阶段可达），并核实 vendor 里 ECharts 仅有的一处 `new Function` 位于 `JSON.parse` 的兜底分支（现代浏览器永不执行）。`style-src` 保留 `'unsafe-inline'`（ECharts 与页面模板大量内联样式，去掉会整片白屏）。外链白名单来自**实测清点**，全站只有 2 处：Steam CDN 封面（`img-src`）与 B 站播放器 iframe（`frame-src`）。
- [x] **公网形态关闭交互文档**（`main.py`，2026-09-11）：`PUBLIC_MODE=1` 时 `docs_url`/`redoc_url`/`openapi_url` 全置 `None` —— 实测带准入口令访问 `/docs`、`/redoc`、`/openapi.json` 原本都是 **200**，等于把全部 admin 端点、参数名、字段约束摊给任何持口令的人。本地/CI（`PUBLIC_MODE≠1`）保持默认便于调试。

- [x] **展示端收口：不采集、不标注、不改采集任务**（`src/runtime_mode.py` + `src/api/routers.py` + `src/pipeline.py`，2026-09-11）—— 填掉「线上 admin 页看着能采」的陷阱：
  - **形态判定** `runtime_mode.display_only()`：**默认跟随 `PUBLIC_MODE`**（公网形态本身就是展示端，故 VPS **不需要在 `.env` 里额外记开关** —— 少一个能忘的地方）；本地开发（`PUBLIC_MODE=0`）完全不受影响。确需一台可写的公网实例才显式设 `DISPLAY_ONLY=0`。
  - **API 层** `routers.require_writable`（挂在 `admin_router`，位于 `require_admin` **之后**）：GET/HEAD/OPTIONS 放行（线上仍能"看"任务列表与 backfill 状态），POST/PATCH/DELETE 一律 **403**；未登录仍是 **401**（不向匿名者透露实例形态）。因为它是**依赖**，对不存在的 id 也返回 403 而非 404 —— 拒绝的理由是"实例只读"，与目标行是否存在无关。
  - **pipeline 层** `run_pipeline()` 开头直接 `raise`（**采集与标注一起挡**，且连采集器初始化都不进）：即便将来有别的入口触发采集，也拦得住。B 站队列 `runner` 走的是同一个 `run_pipeline`，同样覆盖。
  - **可见性**：启动时往日志写一行形态声明（`grep 展示模式 logs/web.log` 即可确认这台能不能写/能不能采），不留"以为能采"的暗坑。
- [x] **修复 `.env` 的 `LOG_LEVEL` 在 Web 端空转**（`main.py::_setup_logging`，2026-09-11）：uvicorn 只给 `uvicorn.*` 配 handler 且 `propagate=False`，root logger **一直没有 handler** → `src/api/*` 的 `log.info` 全被 Python 的 lastResort（只处理 WARNING+）丢弃。实测线上 `logs/web.log` 里**一条 `voc.api` 日志都没有**（只有 uvicorn 访问行），`.env` 写着 `LOG_LEVEL=INFO` 却是空转。修法：`create_app` 在 `load_dotenv` 之后按 `LOG_LEVEL` 配 root（用 `basicConfig`，root 已有 handler 时自动 no-op，不与 uvicorn / pytest 抢配置）。修完 `voc.api` 与 `voc.api.access_log` 的 INFO 都能看到，且未观察到第三方库刷屏。

> 单测：`tests/test_p1_hardening.py`（**20 例**，覆盖缓冲/落库/失败吞掉/清理/中间件记与跳/崩溃留痕/公网自检四态/**交互文档开关两态**/**展示端形态（拒绝 pipeline + 默认继承 PUBLIC_MODE）**/**日志配置（LOG_LEVEL 不空转）**/B站伪名稳定性与 profile 剥离）；`tests/test_api.py` 另加 **2 例**（展示端下 admin 写 403 而读 200、未登录仍 401）。`tests/conftest.py` 全局默认 `ACCESS_LOG_ENABLED=0` 防测试污染真实审计库。全量 pytest **257 passed / 1 skipped**。

> **VPS 已部署并实测通过（2026-09-11）**：上传 `access_log.py`/`main.py`/`service.py`（+2 测试文件）→ `.env` 追加 `PUBLIC_MODE=1`、`ENTRY_AUTH_ENFORCED=1`、`ACCESS_LOG_*` 共 6 项（部署前 `.env` 已备份为 `.env.bak_<时间戳>`，部署脚本自带「起不来即自动回滚 `.env`」）→ `systemctl restart voc-web`。实测结果：
> 1. `voc-web` **active**，`/api/health` → `{"ok":true,"comments":18916}`；
> 2. 日志出现 `PUBLIC_MODE=1 但 COOKIE_SECURE≠1` 告警 → 证明 `_check_public_mode` **确已执行且通过**（不满足前置条件会 fail-closed 拒启，服务此刻是 active 即反证）；
> 3. `data/access_log.db` 已生成（8 列 `id/ts/ip/method/path/status/anon/duration_ms`）；`/api/targets`、`/api/comments`、`/api/agent/*` **入库**，`/api/health` **不入库**（噪声过滤生效）；
> 4. **B 站脱敏生效**：库内 `author=落花影I` → API 返回 `B站用户ee1a8f6a`，`extra.profile` 仅剩 `level/vip/sex`（`uname`/`official` 已剥离）；
> 5. **SSE 未被中间件破坏**：`POST /api/agent/chat` → `200` + `content-type: text/event-stream`，正常吐 `event: token`；对应审计行 `duration_ms≈2588` 且 `anon=p1verify` —— 证明纯 ASGI 中间件**等整条流结束才落库、未缓冲流**；
> 6. 公网 `:8443` 无凭据仍 `401`（Caddy 准入未受影响）。
>
> 部署环境约束：`voc-web.service` 的 `ProtectSystem=strict` + `ReadWritePaths=/home/voc/voc-platform/data /home/voc/voc-platform/logs` 已覆盖 `data/access_log.db`；`ACCESS_LOG_DB=data/access_log.db` 相对 `WorkingDirectory=/home/voc/voc-platform` 解析为绝对路径。unit **无 `EnvironmentFile`**，环境变量全部由应用内 `load_dotenv` 读取 → 改 `.env` 后只需 `restart`，无需 `daemon-reload`。

### 5.5.4 备案前的 HTTPS：已用「自签 TLS」落地（2026-09-11 · P0-A）

明文 HTTP 下，basic_auth 口令、admin 密码、admin session cookie、Agent 对话全文**均可被中间人嗅探** —— 拿到 gate 口令即全站入口，拿到 admin cookie 即后台。这是唯一"性质级"残留风险，**已修**：

- **已上线**：`https://134.175.115.248:8443`，Caddy `tls internal`（内部 CA 自签，证书含 `IP Address:134.175.115.248` 的 SAN）。加密强度与 CA 证书一致，只是"没花钱买信任"——浏览器首次访问提示不受信任，点「高级 → 继续前往」即可（每个浏览器点一次）。
- **踩坑（必读）**：客户端用 **IP 字面量**访问时**不发 SNI**（RFC 6066 禁止把 IP 当 SNI），Caddy 因此选不到证书 → 直接回 `TLS alert internal error(80)`，**浏览器与 curl 全部连不上**；而带 `-servername` 的 `openssl s_client` 却能握手成功，极易误判成"证书没问题"。解法：全局块加 `default_sni <本机 IP>`。详见 §7A-3。
- **刻意不加 HSTS**：IP 字面量 + 自签的组合下，若浏览器真记住 HSTS，证书警告会变成"不可绕过"，测试者就被锁在门外。留到 §7B 换真证书时再加（§7B 片段已含 HSTS 行）。
- **仍可选（想要"无警告"的绿锁）**：
  1. 把内部 CA 根证书分发给测试者导入一次：`/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt`（导入后不再弹警告；私钥不出服务器）；
  2. **DNS-01 签 `erself.site` 真证书**：只写 TXT 记录、**不依赖 80/443 连通**，绕开备案阻断；用户访问 `https://erself.site:8443`（非标端口不受阻断）。需 DNSPod API token（apt 版 Caddy 无 dnspod 模块，需 `xcaddy` 编译或用 `acme.sh` 外部签发）；
  3. **Cloudflare 代理**：域名接 CF 免费版，用户侧直接 HTTPS 且隐藏源站 IP。
- **备案通过后**：按 §7B 换域名 + Let's Encrypt 真证书 + 加 HSTS；`.env` 的 `COOKIE_SECURE` 保持 `1`。

> ⚠️ 上了 TLS 后 `http://` 打 8443 会返回 **400**（`Client sent an HTTP request to an HTTPS server`），这是正常的。**发给内测人员的链接必须是 `https://`**。

### 5.5.5 必做运维配置（非代码）

- [x] **fail2ban** 安装 + 给 Caddy 加 jail（防 basic_auth 爆破）（2026-09-11 落地，`sshd` + `voc-caddy-401` 两 jail active；配置见 §7A-2）。实测参数：`voc-caddy-401` = 20 次 / 10 分钟 → 封 1 小时；`sshd` = 5 次 / 10 分钟 → 封 10 分钟。
- [x] **ufw 收敛**（2026-09-11）：只放通 **22 + 8443**。原先还开着 80/443，但**没有任何进程监听**（纯暴露面，等 §7B 域名阶段再按需开），已删规则。密钥登录已做（`PasswordAuthentication no`）。
- [x] **系统补丁 + 重启**（2026-09-11）：内测前积压 **172 个可升级包（115 个来自 security 源）** → ①`apt-get upgrade` 装掉 167（`--force-confold` 保住 `/etc/caddy/Caddyfile`，否则 caddy 包内 conffile 会盖掉我们的配置）；②余 5 个（内核 / `linux-firmware` / `fwupd`）再 `apt-get dist-upgrade`（**0 删除、27 新装**，新装的全是 `linux-firmware-*` 拆分包）→ **剩余可升级 0**；③`reboot`：运行内核 `6.8.0-124` → **`6.8.0-139`**，`reboot-required`（`libc6`/`apparmor`/`linux-base`）清零。升级前把 `Caddyfile` 与 `.env` 备份到 `/root/`。**重启后复验**：6 个服务全 active + enabled、`https` 无凭据仍 401 / 有凭据 200、`/docs` 仍 404、内部 CA 证书**复用未重签**（时间戳不变 ⇒ 测试者不会因重启看到新的证书警告）、主库 `comments=18916` 与审计库行数均完好。
- [x] **Caddy 访问日志轮转**（2026-09-11）：原先 `output file` 无任何轮转 → 单文件无限增长（每行含完整请求头，约 1KB）。已加 `roll_size 20MiB` / `roll_keep 5` / `roll_keep_for 30d`。
- [x] **SSH `PermitRootLogin` 收敛**（2026-09-11）：原为 `yes`（root 无 `authorized_keys`、密码登录又关，实际登不进来，属潜在风险）。改法用 **drop-in** `/etc/ssh/sshd_config.d/99-voc-hardening.conf` —— Ubuntu 的 `sshd_config` 在**顶部** `Include` 该目录，sshd 取"**先出现者生效**"，故 drop-in 里的值能覆盖主文件里的 `PermitRootLogin yes`，且不受包升级 conffile 影响。流程：写 drop-in → `sshd -t` 语法校验（不过则删掉回退）→ `systemctl reload ssh`（不重启、不断连接）→ 实测 `sshd -T` 输出 `permitrootlogin without-password`（`prohibit-password` 的别名），`PasswordAuthentication no` 保持不变。
- [ ] **一人一个 gate 账号**：当前 `tester01/tester02` 两人共享，出问题无法定位到人。Caddy 日志**会**记 basic-auth 用户名（口令值被 Caddy 默认打码成 `[]`，已实测解码验证），故给每人加一行即可溯源，成本≈0。
- [ ] **备份加密**：`backups/voc-*.db` 含 B 站用户数据。

---

## 6. 维护手册

### 6.1 日常（每日自动，无需人工）

> **Cron 跑了什么**（无人值守）
> - UTC 18:00（= 北京时间 02:00）：`daily_incremental_collect.py` 跑完当日 Steam 单机增量 + B站 run-due
> - UTC 19:00（= 北京时间 03:00）：`check_daily_collect.py` 检查 02:00 是否成功/未跑，失败则补采
> - UTC 03:00（周日）：VACUUM + 滚动备份 DB（保留 5 份）
> - 每 10 分钟：DB 健康检查（评论数能查询）

### 6.2 每周（10 分钟）

```bash
# 查看日志有无报错
ssh voc@<VPS> 'tail -50 ~/voc-platform/logs/cron.log'

# 检查磁盘空间
ssh voc@<VPS> 'df -h /home && du -sh ~/voc-platform/{data,logs,backups}'

# 检查 Caddy 证书有效期（应自动续签到 < 90 天）
ssh voc@<VPS> 'echo | openssl s_client -servername voc.example.com -connect voc.example.com:443 2>/dev/null | openssl x509 -noout -dates'
```

### 6.3 每月（30 分钟）

```bash
# 1. 系统更新
ssh voc@<VPS> 'sudo apt update && sudo apt upgrade -y && sudo reboot'  # 重启后 Streamlit 自动拉起

# 2. 验证 smoke test（确认代码改动没破主干）
ssh voc@<VPS> 'cd ~/voc-platform && source .venv/bin/activate && python scripts/smoke_test.py'

# 3. 把 DB 备份推到外部存储（防 VPS 单点故障）
# 方案：rsync 到另一台 VPS / rclone 到 Cloudflare R2 / B2
ssh voc@<VPS> 'rclone copy ~/voc-platform/backups r2:voc-backups/$(date +%Y%m)'

# 4. 清理旧日志（> 30 天的）
ssh voc@<VPS> 'find ~/voc-platform/logs -name "*.log" -mtime +30 -delete'
```

### 6.4 故障应急

> **症状 → 排查 → 修复**
>
> - **公网访问 502**：Caddy 与 Streamlit 失联 → `sudo systemctl restart voc-streamlit` → 仍 502 检查 `logs/streamlit.log`
> - **公网访问超时**：VPS 防火墙可能被改 → `sudo ufw status` → SSH 进不去则用 Oracle 控制台 VNC
> - **DB 文件损坏**：从 `backups/voc-YYYYMMDD.db` 选最近一份 cp 回 `data/voc.db`
> - **cron 没跑**：检查 `crontab -l`、`/var/log/syslog`、VPS 时区（`timedatectl`，应设为 UTC）
> - **DeepSeek 余额耗尽**：登录 platform.deepseek.com 充值；临时切 `ANALYZER_PROVIDER=qwen` / `glm`

---

## 7. 数据通道（私有构建 → 公网服务）

> **形态 A 的数据流是「同机直读」，不需要任何外发通道**
>
> - cron 写 `data/voc.db` → Streamlit 读 `data/voc.db`
> - 两个进程在同一文件系统，**没有网络传输、没有外部 DB 服务、没有 GitHub Release**
> - 公网层（Streamlit + Caddy）只能"读已渲染的视图"，**读不到 DB 路径**
>
> **与原 P6 方案（GitHub Release 累积）的区别**
>
> | 维度 | 形态 A（自托管） | 原 P6（GH Release 累积） |
> |---|---|---|
> | DB 物理位置 | VPS 本地磁盘 | GitHub Release asset（公开可下） |
> | 公网可下载 DB | ❌ 不可能 | ⚠️ 任何人 wget |
> | 跨日累积 | 同机增量写，天然累积 | 每天拉 release + 增量 + 上传 |
> | 维护量 | 需自己管 VPS / 备份 | GH Actions 免费托管 |
> | 数据私密性 | ✅ 完全私密 | ⚠️ 仓库公开 → 公开 |
>
> **从 P6 迁到形态 A**：保留 `daily_incremental_collect.py` 和 `targets.yaml`，只是把 `--no-download --no-upload` 作为默认（不依赖 GH Release）。GitHub Actions 可以**完全停掉**（关 workflow / 删 `.github/workflows/daily-*.yml`）。

---

## 8. 部署后的项目结构（VPS 上）

```
/home/voc/voc-platform/
├── .venv/                       # Python 虚拟环境
├── .env                         # Secrets（权限 600）
├── src/                         # 采集器 / 分析器 / 存储（私有，root 也只读）
├── scripts/                     # 运维脚本
├── config/                      # 业务配置（targets / topics / prompts）
├── data/
│   └── voc.db                   # SQLite 单文件，权限 600（唯一权威源）
├── logs/
│   ├── cron.log                 # 每日采集日志
│   ├── streamlit.log            # 仪表盘日志
│   └── health.log               # 健康检查日志
└── backups/
    └── voc-YYYYMMDD.db          # 周日滚动备份（保留最近 5 份）
```

---

## 9. 验收清单（首次部署后逐项勾选）

- [ ] 公网 `https://voc.example.com` 能打开 Streamlit 仪表盘
- [ ] 仪表盘能展示 6 款 Steam 单机的情感分布 / 主题 TOP10 / 词云
- [ ] `curl https://voc.example.com/data/voc.db` 返回 404 或连接拒绝
- [ ] `curl https://voc.example.com/.env` 返回 404
- [ ] `ssh voc@<IP> -p 22222` 能登录；密码登录被拒
- [ ] `sudo ufw status` 显示仅 80/443/SSH 端口开放
- [ ] `sudo systemctl status voc-streamlit` 显示 active (running)
- [ ] `ls -la data/voc.db` 显示权限 600，owner voc
- [ ] `crontab -l` 显示 3 条任务（cron / 备份 / 健康检查）
- [ ] 手动跑 `python scripts/ops/daily_incremental_collect.py --no-download --no-upload` 成功
- [ ] `python scripts/smoke_test.py` 全绿
- [ ] GitHub Actions 两个 workflow 已关闭或删除
- [ ] Caddy 证书有效期 > 60 天（`echo | openssl s_client ... | openssl x509 -noout -dates`）

---

## 10. 不在形态 A 范围的事

- ❌ **多用户鉴权**：形态 A 默认公网可访问，无登录；如要"团队成员也鉴权"，在 `app.py` 加 `st.auth`（Streamlit 1.30+）或换 FastAPI + JWT
- ❌ **写权限**：仪表盘只读 DB；不允许用户触发新采集或修改数据（那是 cron 的活）
- ❌ **CDN / 全球加速**：单 VPS 部署，跨地域访问慢；中国大陆访问 Oracle / 国外 VPS 可能卡，需要再套 Cloudflare 或迁国内云
- ❌ **实时流式更新**：cron 是日级；用户刷新页面才看得到当天数据（业务上也够用）

---

## 11. ①b 落地清单（2026-09-11 · 逐项勾选）

**外部资源（工程师）**
- [x] 购买轻量服务器：**腾讯云轻量 · 广州（大陆）** `134.175.115.248:22`，登录用户 `ubuntu`，密钥对名 `lynx-web-SSH-kye`（2026-09-11）
- [x] 购买域名：**`erself.site`**（`.site` 属工信部批复后缀，可备案；2026-09-11）
- [x] **SSH 登录打通**：有效私钥为 `~/.ssh/k_lynx_web.pem`，`ssh -o BatchMode=yes ubuntu@134.175.115.248` 可直连（2026-09-11）
- [x] 轻量防火墙放通 **8443/TCP**（内测，2026-09-11 公网实测 200）；80/443 待备案通过后放通
- [x] 提供公网 IP / SSH 端口 / 登录用户（2026-09-11）
- [ ] （大陆节点）域名**实名认证**须与备案主体一致 → 提交 ICP 备案并等待通过

> **历史阻塞（已解决）**：早期用 `C:\Users\44481\.ssh\lynx-web-key.pem`（指纹 `SHA256:At5gFb56…`）登录被拒
> （`Permission denied (publickey)`）→ 改用可用的 `~/.ssh/k_lynx_web.pem` 后正常。

**开发（本地仓库）**
- [x] `scripts/ops/push_db_to_vps.ps1`（VACUUM INTO 本地快照 → SHA256 → scp → 远端**原位** `sqlite3 .backup()` 回灌；失败返回非零但**不阻塞采集**）+ `tests/test_daily_incremental_collect.py` 新增 8 例（2026-09-11 端到端实测通过）
- [x] 同步触发：并入 02:00 采集链路末尾（`daily_incremental_collect.py --push-db`，默认关；`register_local_collect_task.ps1` 的 02:00 任务已带该 flag，`-NoPushDb` 可退回）
- [ ] 提交现有未入库改动（Agent 73 例 + 前端 + 文档），保证可回滚

**VPS 端**
- [x] §4 步骤 1–5：voc 用户 / 系统包 / 代码 / `.env`（600）/ data 目录（2026-09-11）
- [x] §6.5 `voc-web.service`（uvicorn `127.0.0.1:8000`，enabled + active）+ systemd 加固（**ProtectHome 必须 read-only**）
- [x] §7 Caddy 反代：apt 2.6.2，`:8443` 已上线；**2026-09-11 起为自签 TLS**（`tls internal` + `default_sni`，见 §7A / §7A-3；域名真证书待备案后切 §7B）
- [x] §5 安全清单：ufw 已启用并**收口为 22 + 8443**（原 80/443 无监听，已删）、DB 600、`.env` 600、SSH 密钥登录（`PasswordAuthentication no`）；**fail2ban 已装**（`sshd` + `voc-caddy-401` 两 jail active，见 §7A-2）
- [x] 确认 VPS 不装 ML 依赖；确认 `/data/voc.db`、`/.env`、`/data/access_log.db` 公网 404（2026-09-11 实测）
- [x] §5.5.3 四条 P1 全部落地：审计日志 / `PUBLIC_MODE` 自检 / B站脱敏 / **CSP+Permissions-Policy**；另有**公网关交互文档**、**Caddy 日志轮转**、**系统补丁 172→5**

**验收**
- [x] 内测（**HTTPS**）`https://134.175.115.248:8443/` 打开看板；`/api/health` 返回 `{"ok":true,"comments":18916}`（2026-09-11，自签证书需在浏览器点一次「继续前往」）
- [ ] 域名 HTTPS 打开三看板（待备案通过 + §7B）；**数据一致已达成**：`push_db_to_vps.ps1` 实测推送后远端 `comments=18916` 与本地一致（2026-09-11）
- [x] `/api/agent/chat` 流式对话可用：公网 `:8443` SSE 实测收到 `token` + `done`、会话落库 `['user','assistant']`；**2026-09-11 在 TLS/HTTP-2 下复测仍为真流式**（未被反代缓冲），见 §7A-3
- [x] admin 登录 + 采集任务增删改（CRUD）：本地 TestClient（真实 app + 真实库）验收 **9/9** 通过（2026-09-11）；线上登录返回 `httponly; samesite=lax; secure` cookie
- [x] `https://134.175.115.248:8443/data/voc.db` → 404；`/.env` → 404；`/docs` · `/redoc` · `/openapi.json` → **404**（2026-09-11 实测）
- [ ] 静态快照站（EdgeOne）与 VPS 版并存互不影响，手册交叉引用

---

## 📋 版本记录

| 更新时间 | 内容 | 原因 |
|---|---|---|
| 2026-09-11 | **展示端代码级收口（填「看着能采」陷阱）+ 修 Web 端 `LOG_LEVEL` 空转**：①新增 `src/runtime_mode.py::display_only()`，**默认跟随 `PUBLIC_MODE`** → 公网形态自动成为展示端，**VPS 无需在 `.env` 加键**（少一个能忘的开关）；本地开发不受影响；确需可写公网实例才显式 `DISPLAY_ONLY=0`。②`routers.require_writable` 挂 `admin_router`（在 `require_admin` 之后）：GET/HEAD/OPTIONS 放行、写操作 **403**、未登录仍 **401**、对不存在 id 也 403（依赖先于 handler）。③`run_pipeline()` 开头 `raise`，**采集与标注一起挡**（含 B 站 runner 路径）。④启动日志写一行形态声明，可用 `grep 展示模式 logs/web.log` 确认。⑤顺带修：uvicorn 只配 `uvicorn.*` 且 `propagate=False` → root 无 handler → 应用自身 `log.info` 全被 lastResort 丢弃（**实测线上一条 `voc.api` 日志都没有**，`LOG_LEVEL=INFO` 空转）→ `create_app` 按 `LOG_LEVEL` 配 root。⑥测试 +5 → 全量 **257 passed / 1 skipped**。⑦VPS 零副作用实测：读 200 / 写 403 / 未登录 401 / `collect_tasks` 仍 8 行 / `/docs` 仍 404。**关键坑（TLS 段）**：`default_sni` 让展示端在 IP 直连下也能握手。详见 §0.5 末条 + §5.5.3 两条新勾选 | 工程师「把这个陷阱填掉……VPS 不要采集，也不要标注」 |
| 2026-09-11 | **内测前收尾：补丁清零 + 重启换内核 + SSH root 收敛**：①`apt-get dist-upgrade` 装掉余下 5 个（内核 / `linux-firmware` / `fwupd`，**0 删除、27 新装**，新装的全是 `linux-firmware-*` 拆分包）→ **剩余可升级 0**；②`reboot` 后运行内核 `6.8.0-124` → **`6.8.0-139`**，`reboot-required`（`libc6` / `apparmor` / `linux-base`）清零；③`PermitRootLogin` 由 `yes` 改为 **drop-in** `/etc/ssh/sshd_config.d/99-voc-hardening.conf`（`sshd_config` 顶部 `Include` 该目录、**先出现者生效**，故能覆盖主文件里的 `yes` 且不怕包升级 conffile）→ `sshd -t` → `reload ssh`，实测 `permitrootlogin without-password`（= `prohibit-password`）；④**重启后复验**：6 个服务全 active + enabled、无凭据 401 / 有凭据 200 / `/docs` 404 / 明文 `:8443` 400 / 5 项安全头仍在、**内部 CA 证书复用未重签**（时间戳不变 ⇒ 测试者不会因重启看到新的证书警告）、主库 `comments=18916`、审计库 180 行、公网侧（本机 curl）同样通过。§5.5.5 勾选同步 | 工程师「1. 重启，现在。2. 改。」 |
| 2026-09-11 | **P0-A 自签 TLS 上线 + P1 五项收口（VPS 实测）**：①**P0-A**：Caddyfile 由 `http://:8443` 切 `https://134.175.115.248:8443` + `tls internal`（内部 CA 自签，证书 SAN = 本机 IP）。**关键坑**：客户端用 IP 字面量访问**不发 SNI**（RFC 6066 禁止），Caddy 因此选不到证书 → 回 `TLS alert internal error(80)`，`curl`/浏览器全连不上；而带 `-servername` 的 `openssl s_client` 却能握手，极易误判"证书没问题"→ **解法：全局块 `default_sni <IP>`**，加后 `curl` 首探测即 `200`。刻意**不加 HSTS**（IP 字面量 + 自签下，若被浏览器记住 HSTS，证书警告会变"不可绕过"，测试者被锁在门外）。②**P1-2**：`PUBLIC_MODE=1` 时关 `/docs`/`/redoc`/`/openapi.json`（实测带 gate 口令原本 **200**，等于把全部 admin 端点/参数名/字段约束摊开）→ `main.py` 增 `_is_public_mode()`，线上转 **404**，本地/CI 保持可用。③**P1-4 CSP/Permissions-Policy**：为让 `script-src` 做到**纯 `'self'`**（无 `unsafe-inline`/`unsafe-eval`），去掉前端唯一内联事件处理器（`compare.js` 的 `<img onerror>` → `document` 级**捕获**监听，因 `error` 不冒泡），`index.html` 缓存串 bump `compare.js?v=20260911a`；核实 ECharts 仅一处 `new Function` 位于 `JSON.parse` 兜底分支；`style-src` 保留 `'unsafe-inline'`；`img-src`/`frame-src` 白名单由实测清点得出（Steam CDN 封面 + B站播放器 iframe）。④**P1-3** 日志轮转 `roll_size 20MiB`/`roll_keep 5`/`roll_keep_for 30d`（原先无轮转、单文件无限增长）。⑤**P1-5** ufw 收口 **22 + 8443**（80/443 无监听）。⑥**P1-1** 系统补丁 **172 → 5**（含 115 个 security 源），用 `--force-confold` 保住 `/etc/caddy/Caddyfile`。⑦`COOKIE_SECURE=1` → 线上登录返回 `httponly; samesite=lax; **secure**` cookie。⑧`.env.example` 更新 `COOKIE_SECURE` / `PUBLIC_MODE` 注释。**回归**：SSE 在 **HTTP/2** 下仍真流式（`event: token`/`done` 完整）、审计照常落库（174 行）、`voc-web`/`caddy`/`fail2ban`/`ufw`/`ssh`/`unattended-upgrades` 全 active+enabled、5 项安全响应头全命中；pytest **252 passed / 1 skipped**。**过程中的事故与纠正**：把上一轮会话遗留的 `/tmp/Caddyfile.new`（同名但**旧 gate 口令哈希**）误当候选文件 `install`，导致准入口令短暂回退 → `diff` 定位后立即用备份还原（md5 一致）+ 新旧口令双向验证，并给手册补了两道守卫（候选文件唯一名 + 属主校验 + 哈希与线上逐字比对；CRLF 必须 `sed -i 's/\r$//'` 后再比）。详见 **§5.5.4 / §5.5.5 / §7A / §7A-3** | 工程师「开始P0-A方案，接着做P1」：把"明文传输"这一唯一性质级风险从"待办"推到"线上加密 + 端到端实测"，并收口 P1 五项 |
| 2026-09-11 | **§5.5.3 P1 部署到 VPS + 端到端实测（6/6 通过）**：`install` 部署 `access_log.py`/`main.py`/`service.py` + 2 测试到 `/home/voc/voc-platform/`（属主 `voc:voc`）；`.env` 追加 `PUBLIC_MODE=1`、`ENTRY_AUTH_ENFORCED=1`、`ACCESS_LOG_ENABLED/DB/RETENTION_DAYS/FLUSH_SEC`（部署脚本先备份 `.env`，失败自动回滚 + 重启）；`restart voc-web`。**实测**：①active + `/api/health` `{"ok":true,"comments":18916}`；②日志 `PUBLIC_MODE=1 但 COOKIE_SECURE≠1` = 自检已执行且未 fail-closed；③审计库 8 列生成，业务路径入库、`/api/health` 与静态资源不入库；④B 站 `落花影I`→`B站用户ee1a8f6a`、`profile` 剥离 `uname/official`；⑤SSE `200`+`text/event-stream` 正常吐 token，审计行 `duration_ms≈2588`（未缓冲流）；⑥公网 `:8443` 无凭据 `401`。详见 **§5.5.3 末尾「VPS 已部署并实测通过」**。 | 工程师「1. VPS执行」：把 P1 从"代码就绪"推到"线上启用 + 实测" |
| 2026-09-11 | **§5.5.3 P1 代码落地（3/4）+ `.env.example` 同步**：①**审计日志** `src/api/access_log.py` —— 与原计划两处有意偏离：**单独 DB** `data/access_log.db`（主库每晚被 ①b 整库覆盖，审计表放主库会被清空）+ 列为 7 个（增 `method`/`duration_ms`，不记 query/body）；请求路径仅写内存 deque、后台 5s 批量落库、**失败即丢弃**（不拖垮业务）、30 天保留、静态资源与 `/api/health` 不入库；中间件为**纯 ASGI**（避 `BaseHTTPMiddleware` 给 SSE 多套转发）。②**`PUBLIC_MODE=1` 启动自检** `main.py::_check_public_mode` —— fail-closed 缺件拒启（`SESSION_SECRET_KEY`/`ADMIN_PASSWORD_HASH`/额度>0/`ENTRY_AUTH_ENFORCED=1`）；`COOKIE_SECURE`、`ACCESS_LOG_ENABLED` 仅告警。③**B 站脱敏** `service.py::_public_comment` —— `author` 昵称→sha1 前 8 位稳定伪名、`extra.profile` 删 `uname`/`official` 留 `level`/`vip`/`sex`、库里留原文只脱对外层；`Comment.to_dict()` 无 `author_id` 故不泄 mid。④**CSP 留待做**。⑤`tests/test_p1_hardening.py` 15 例 + `tests/conftest.py` 全局默认关审计（防污染真实库）；全量 pytest **250 passed / 1 skipped**。⑥`.env.example` 增 P1 段（`PUBLIC_MODE`/`ENTRY_AUTH_ENFORCED`/`ACCESS_LOG_*`） | 工程师「三、都做」：把 §5.5.3 的 P1 从"建议"推到"代码就绪 + 单测覆盖 + 文档一致" |
| 2026-09-11 | **①b 数据通道落地（E 项）+ 内测功能验收（C/D 项）**：①新增 `scripts/ops/push_db_to_vps.ps1`——5 步（本地 `wal_checkpoint(TRUNCATE)` + `VACUUM INTO` 快照 → 本地 SHA256 → `scp` 到 `/tmp/voc.db.new` → 远端 SHA256 + `integrity_check` + 评论数校验 → 远端**原位** `sqlite3.Connection.backup()` 回灌）；**有意偏离原设计**：原写「远端 `mv` 原子替换」对本站不安全（uvicorn 连接池持旧 inode → 静默读旧库不报错），改为原位 `.backup()` 逐页重写 + `busy_timeout=60000` 兜并发；参数 `-DbPath`/`-Remote`/`-RemoteDb`/`-Identity`/`-DryRun`/`-RestartService`（默认关）。②`daily_incremental_collect.py` 新增 `push_db_to_vps()`（任何失败 → warning + 返回 False，**不改采集退出码**）+ `--push-db`（默认关）；`register_local_collect_task.ps1` 的 02:00 任务追加 `--push-db` + `-NoPushDb` 退回开关。③`tests/test_daily_incremental_collect.py` +8 例（默认关 / 传 flag 推一次 / 顺序 collect→summary→push / 失败不改退出码 / 脚本缺失 / 非零 rc / 无 powershell / 显式 utf-8 解码）。④**端到端实测**：dry-run 快照 `integrity=ok` / `comments=18916` / 116.9 MB；真实推送两端 SHA256 一致（`1423887f…`）、远端 `RESTORE_OK`、`live_comments_after=18916`；推送后 `/api/health` 仍 `{"ok":true,"comments":18916}`、`voc-web` active、DB 600、无临时文件残留；全量 pytest **236 passed**。⑤**C 项**（admin 登录 + 采集任务 CRUD）本地 TestClient（真实 app + 真实库）**9/9 通过**；**D 项**（Agent SSE / tool 调用 / 落库 / 归属护栏）通过。⑥§0.5 同步 5 步流程 + `mv` 偏离警告 + 失败语义 + 触发时机 + 参数；§11 开发/验收勾选 | 工程师「C,D,E 都做」：把 ①b 的「本地→VPS」数据通道从设计变为可用脚本并并入 02:00 链路，同时完成内测功能验收 |
| 2026-09-11 | **§7A 手册与线上实测对齐（Caddy 2.6.2 指令名坑 + 实测记录表 + fail2ban jail）**：①**指令名坑**——Caddy **2.6.2（apt/universe）里是 `basicauth`**，`basic_auth` 是 **2.8+ 新名**；照 2.8+ 文档写会 `validate` 失败并报 `unrecognized directive: basic_auth`（本站首次部署即踩），§7A 片段统一改 `basicauth` 并加警告；②§7A 补「先备份 + 先 `validate` 再覆盖」安全操作法（改写 `/tmp/Caddyfile.new` → 验证 → 覆盖），`restart`→`reload`；③新增 **§7A-1 实测记录表**：no-auth→**401** / with-auth→**200**；**130 次伪造 XFF 连打 → 200=120 / 429=10，首个 429 恰在 #121**（P0-2+P0-3 同时生效）；200KB→**422**；2MB→**502**（含"为何不改回 413"说明：`expression` 匹配 `Content-Length` 无法兜 chunked）；SSE `ttfb≈1.2s/total≈2.6s` 收到 `token`+`done` 且落库 `['user','assistant']`（P0-4 不阻塞流式）；④新增 **§7A-2 fail2ban jail**（filter 匹配 Caddy JSON 日志 `"status":401`，jail `voc-caddy-401`，`-t` 先测再重启）；⑤§5.5.2 P0-4/P0-5 与 §5.5.5 fail2ban 勾选、§11 fail2ban 由「待装」改「已装」 | 工程师「把你能替我做的都做好」：把本轮实测结论固化进手册，消除"文档写的 Caddy 指令在线上跑不通 / fail2ban 一直待装"类不一致 |
| 2026-09-11 | **§5.5 安全加固 P0-1/2/3/4 落地**：①**P0-2**（代码）`auth.client_ip()` 由 XFF **首段**改取**末段**（反代追加的真实 IP；首段可被客户端伪造 → 原限流可被"每次换一个随机 XFF"绕过）；§7A/§7B Caddyfile 均加 `header_up X-Forwarded-For {remote_host}` 覆盖；新增 2 例回归。②**P0-1**（VPS 配置）§7A 重写为 `caddy hash-password` + `basic_auth { 每人一行 }` 全站准入，含"不带凭据必须 401"验证与撤人步骤。③**P0-3**（代码）通用 `_rate_check` 抽离，新增 `check_public_rate`/`public_rate_limit` 并挂 `public_router`（一处覆盖 11 个公开只读端点，默认 120/min/IP）；`/api/health` 不受影响；新增回归 1 例。④**P0-4**（代码）新增日额度熔断 `AGENT_DAILY_CHAT_LIMIT`(300)/`AGENT_DAILY_CHAT_LIMIT_PER_IP`(50)（UTC+8 自然日、归属校验后扣减）、`AGENT_MAX_TOKENS`(2048) 传入 LLM、并发闸 `AGENT_MAX_CONCURRENCY`(2)+`AGENT_QUEUE_WAIT_SEC`(20)（`stream_chat_guarded` 包装，满则发 `error(busy)`）；新增回归 5 例。`.env.example` 同步 7 个新 env | 工程师「继续」：把 §5.5 的 P0 清单从"待办"变成"已落地代码 + 待执行 VPS 配置" |
| 2026-09-11 | **新增 §5.5 内测期安全加固**：备案前外发链接前的 P0 清单（全站准入 basic_auth / XFF 伪造修复 / 公开端点限流 / Agent 成本熔断 / 请求体上限）+ P1（审计日志 / 公网模式自检 / CSP / B站用户信息脱敏）+ 备案前 HTTPS 两条路（DNS-01 签 `erself.site` / Cloudflare 代理）+ 必做运维配置 | 工程师「备案前要给内测人员访问」：把安全整改固化为可勾选清单，避免"裸 IP + 无鉴权 + 明文"外发 |
| 2026-09-11 | **VPS 内测上线：systemd 常驻 + Caddy 反代**：①`voc-web.service` 落地（`uvicorn src.api.main:app --host 127.0.0.1 --port 8000`，`User=voc`，`Restart=always`，`enable` 开机自启）——**实测坑**：手册原写的 `ProtectHome=true` 会导致 `status=203/EXEC`（`/home` 被挂成空目录、venv 二进制无法解析），已改为 `ProtectHome=read-only` 并同步修正 §6 / §6.5 两处 unit；②Caddy 由 apt 装（2.6.2 / universe 源），§7 重写为「7A 内测 `:8443` 明文反代（**必须显式 `http://:8443`**，否则可能走内部 CA）+ 7B 域名块（`erself.site` 自动 HTTPS）」；③安全加固：`ufw` 启用（22/80/443/8443，先放 22 再 `enable` 保证 SSH 不断）、DB/`.env` 均 600、SSH 密钥登录（有效私钥 `~/.ssh/k_lynx_web.pem`）；**fail2ban 因审批未落地，待装**；④实测：公网 `http://134.175.115.248:8443/api/health` → `{"ok":true,"comments":18916}`、SPA 首页 200、`/data/voc.db` 与 `/.env` → 404；⑤§11 勾选同步 | 工程师「继续任务」：把临时 `nohup uvicorn` 固化为 systemd 常驻服务并接 Caddy 反代 |
| 2026-09-10/11 | **启用变体 ①b + 国内轻量获取 + 域名规划**：新增 §0.5（①b 架构/理由/DB 同步设计/4 项决策）+ §3.3（轻量购买路径 / 地域与备案对比 / **购买界面逐项选择对照表** / 域名与 ICP 备案 / 成本）+ **§3.4（香港→大陆迁移路径与两条采购策略，官方依据：自定义镜像跨地域复制 + 5 天无理由退还限首次）** + **§3.5（域名规划：一个域名放多少站点、四种区分方式、本项目 `voc.` + `snapshot.` 映射、CORS/证书/Cookie/备案五条约束）** + §11 落地清单；头部状态改为「落地准备中」。⚠️ 9/11 发现其中 3.3 对照表与 3.4 曾被并行会话覆盖丢失，已重写并当场校验（同 AGENTS.md 条目丢失同类问题） | 工程师确认 4 项决策（国内轻量 / ①b / 新买域名 / Agent 公开+限流内测）+ 追问购买选项、地域迁移、域名规划三问 |
| 2026-09-02 | 补 Web 看板服务：步骤 6.5（uvicorn :8000 + systemd + 鉴权 env）+ secrets 清单扩到 7 项 + WAL checkpoint 备份注意事项 | Web 实时看板（WEB_DASHBOARD.md）落地，VPS 形态 A 可二选一/并存托管 |
| 2026-08-23 | 初版 | 回应"团队外零数据访问 + 公网可访问"诉求；形态 A 落地架构稿 |