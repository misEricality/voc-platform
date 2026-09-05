# 形态 A · 自托管 VPS 部署（公网可访问 / 数据全私有）

> **用途**：让团队外用户能通过公网 URL 查询仪表盘（图表 + 视图），但**采集代码 / 标注脚本 / 数据库 / API Key / 中间产物全部留在团队控制的 VPS 上**，任何人无法下载或越权访问。
>
> **关联文档**：
> - 部署方案选型（为什么选形态 A / 还有哪几种走法）：[DEPLOYMENT_OPTIONS.md](./DEPLOYMENT_OPTIONS.md)
> - 总路线图：[plan/DEVELOPMENT_PLAN.md](../plan/DEVELOPMENT_PLAN.md)
> - 自动化采集流水线：[AUTOMATION_PIPELINE.md](./AUTOMATION_PIPELINE.md)
> - 字段与存储设计：[DATA_FIELDS.md](./DATA_FIELDS.md) / [DATA_STORAGE_DESIGN.md](./DATA_STORAGE_DESIGN.md)
> - 安全与隐私声明：[SECURITY.md](../SECURITY.md)（如存在）
>
> **最后更新**：2026-08-23
> **状态**：🟡 设计稿（待首次落地后补验收清单）

---

## 0. 一句话总览

> **一台公网 VPS 同时承担两件事**：
> 1. **私有构建层**：跑 `cron` 触发 `daily_incremental_collect.py`（采集 + 分析 + 写 SQLite 单文件 DB）
> 2. **公网服务层**：跑 `streamlit run app.py`（绑 `127.0.0.1:8501`，由 `Caddy` 反代到 443 / 自动 HTTPS）
>
> **两件事在同一台机器，但 DB 文件权限 600、Streamlit 进程以非 root 用户运行**——攻击者最多打到 Streamlit 容器，**碰不到 DB 路径、拿不到 API Key、改不了数据**。

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
# 注：本项目 GH Actions 流水线已切到 UTC 17:00（北京次日凌晨 1:00）以避开 GH Actions schedule 最多 8h 延迟；
# VPS 自托管无此延迟问题，可保留 UTC 00:00（= 北京 08:00）做「早晨第一件事前采集完成」；
# 也可与 GH Actions 对齐改 UTC 17:00（个人偏好）。
# 1. 每日采集 + 标注
0 0 * * * cd /home/voc/voc-platform && /home/voc/voc-platform/.venv/bin/python scripts/ops/daily_incremental_collect.py --no-download --no-upload >> /home/voc/voc-platform/logs/cron.log 2>&1

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
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
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
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/home/voc/voc-platform/data /home/voc/voc-platform/logs

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now voc-web
curl -I http://127.0.0.1:8000/api/health   # 应 200 {"ok":true,"comments":N}
```

> Caddy 反代目标由 `8501`（Streamlit）改为 `8000`（Web 看板）即可——二选一，或继续并存各自绑端口。

### 步骤 7 · Caddy 反向代理 + 自动 HTTPS（3 分钟）

```bash
# 7.1 编辑 Caddyfile（假设域名 voc.example.com）
sudo tee /etc/caddy/Caddyfile <<'EOF'
voc.example.com {
    reverse_proxy 127.0.0.1:8501
    encode gzip zstd
    header {
        # 安全头
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
        # 隐藏 Streamlit 默认标识
        -Server
    }
    log {
        output file /var/log/caddy/voc.log
    }
}
EOF

# 7.2 DNS 解析 voc.example.com → VPS IP（A 记录）

# 7.3 重启 Caddy（自动申请 Let's Encrypt 证书）
sudo systemctl reload caddy

# 7.4 验证
curl -I https://voc.example.com   # 应 200 + HSTS
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
# 在 GitHub 仓库 Settings → Actions → 关闭 daily-collect.yml + bilibili-daily.yml
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

## 6. 维护手册

### 6.1 日常（每日自动，无需人工）

> **Cron 跑了什么**（无人值守）
> - UTC 00:00（= 北京 08:00；本节以 VPS 自托管为准，GH Actions 流水线则切到 UTC 17:00 以避开 8h 延迟）：`daily_incremental_collect.py` 跑完当日 6 款 Steam 单机增量
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

## 📋 版本记录

| 更新时间 | 内容 | 原因 |
|---|---|---|
| 2026-09-02 | 补 Web 看板服务：步骤 6.5（uvicorn :8000 + systemd + 鉴权 env）+ secrets 清单扩到 7 项 + WAL checkpoint 备份注意事项 | Web 实时看板（WEB_DASHBOARD.md）落地，VPS 形态 A 可二选一/并存托管 |
| 2026-08-23 | 初版 | 回应"团队外零数据访问 + 公网可访问"诉求；形态 A 落地架构稿 |