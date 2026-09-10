# 静态快照部署（方案③ · EdgeOne Pages 操作手册）

> **用途**：`DEPLOYMENT_OPTIONS.md` 方案③（静态快照 · 作品集门面）的落地操作手册——
> 把 Steam 单游戏看板 / 游戏对比 / B站视频看板三个页面以预聚合 JSON 快照发布到 EdgeOne Pages。
>
> **关联文档**：
> - 选型依据：[DEPLOYMENT_OPTIONS.md](./DEPLOYMENT_OPTIONS.md)（决策 1/6/7）
> - 被部署的对象：[WEB_DASHBOARD.md](./WEB_DASHBOARD.md)（FastAPI + SPA）
> - 每日采集链路：[AUTOMATION_PIPELINE.md](./AUTOMATION_PIPELINE.md)
>
> **最后更新**：2026-09-07
> **状态**：✅ 开发完成，本地验证通过；**首次发布待配置 EdgeOne API Token**

---

## 1. 架构与数据流

```
data/voc.db（单一权威源，本机）
   │  src/api/service.py 聚合函数（直接复用，不重写 SQL）
   ▼
scripts/ops/export_static_snapshot.py
   │  输出 data/exports/snapshot/（5~6 MB）：
   │  ├── index.html            静态版入口（裁剪系统管理导航 + 注入开关）
   │  ├── src/ vendor/          前端静态资源（缓存串 stamp=快照日）
   │  ├── covers/               data/covers 封面拷贝
   │  └── snapshot/
   │      ├── manifest.json     路由表：端点+参数 → JSON 文件
   │      └── api/*.json        预聚合数据（383 个，~2 MB）
   ▼
scripts/ops/publish_static_snapshot.ps1
   │  edgeone pages deploy ./data/exports/snapshot -n <项目名> [-t $EDGEONE_API_TOKEN]
   ▼
EdgeOne Pages CDN（公网，永不宕机）
```

**前端静态模式**（零重写）：`product/web/src/api.js` 的 `API.get` 在
`window.STATIC_SNAPSHOT=true`（静态版 index.html 注入）时改读 manifest 路由表——
「端点 + 查询参数完全匹配 → 返回对应 JSON」；未收录的筛选组合抛出明确错误，
由页面现有 catch/toast 降级。实时看板模式零影响（开关为 falsy 时走原逻辑）。

---

## 2. 快照内容与收敛策略

| 维度 | 收敛方式 |
|---|---|
| Steam 游戏 | monitored 白名单（yaml ∪ collect_tasks）中有数据的游戏；**零数据目标跳过**（实时 API 404 语义，静态页拿 null 会崩） |
| 时间窗 | `all / 30d / 7d / 1d` 四档 × 每游戏导出（「近 N 天不含当天」口径与 `dashboard.js windowParams` 一致）；`custom` 自选时间不支持（toast 降级） |
| 颗粒度 | comment / opinion 双档全导（overview + L1 主题） |
| 列表样本 | 原声/观点列表每组合只导前 `--list-pages`（默认 3）页 × 10 条（**决策 6：不放原始评论全量**）；主题/情感筛选、page>N 不支持（降级提示） |
| 游戏对比 | 静态版**强制累计口径**（同期窗口依赖运行时动态计算，compare.js 静态守卫隐藏同期按钮）；L2 极性（负向/正向）全导 |
| B站视频 | 逐 fetched 视频导出：overview（无 grain 参数形态）/ L1 双图 / 原声列表 likes 序前 3 页 / 弹幕时间轴（固定随机种子保证幂等） |
| games/meta | `targets` 参数用 `*` 通配路由（dashboard 与 compare 的排序串不同但内容等价） |

快照体量（2026-09-07 实测）：8 Steam 游戏 + 5 B站视频 → **383 条路由 / 383 个文件 / 整站 5.4 MB**。

---

## 3. 手动发布步骤（首次）

### 3.1 一次性准备

1. **EdgeOne CLI**（本机）：`npm install -g edgeone`
2. **API Token**：EdgeOne 控制台 → Pages → API Token（CI/CD 用），写入项目根 `.env`：
   ```
   EDGEONE_API_TOKEN=<token>
   ```
   （或设环境变量 / 发布时 `-Token` 参数传入；三选一，优先级从高到低）
3. 项目的**站点根即快照目录**，无构建步骤（纯静态，Pages 默认配置即可）。

> ✅ **2026-09-07 首次发布已完成**：项目 `voc-platform`（Project ID `makers-czitwshepzgx`），
> 静态版首页 = compare（main.js 静态守卫，实时版仍默认 dashboard）；全流程 ~35 秒。
> ⚠️ CLI 提示 `edgeone pages` 命令已 deprecated（仍可用），后续可换 `edgeone makers`。
> ⚠️ 脚本文件必须带 **UTF-8 BOM** 保存——Windows PowerShell 5.1 把无 BOM 的 UTF-8 当
> ANSI 解析，中文字符串会打碎语法（已修复，编辑此 ps1 时注意保留 BOM）。
> ⚠️ 缓存串 stamp **精确到分钟**（`v=YYYYMMDDHHMM`）——同一天多次发布 URL 也不同，
> 否则 EdgeOne CDN 会返回上一版的 main.js 等资源（实测踩过）。

### 3.2 导出 + 发布

```powershell
# 导出 + 生产环境发布（项目不存在会自动创建）
powershell -ExecutionPolicy Bypass -File scripts\ops\publish_static_snapshot.ps1 -Name voc-platform

# 只重新发布（跳过导出，复用现有 dist）
powershell -ExecutionPolicy Bypass -File scripts\ops\publish_static_snapshot.ps1 -Name voc-platform -SkipExport

# 本地先预览再发布（可选）
.venv-ml\Scripts\python.exe -m http.server 8899 --directory data\exports\snapshot
# 浏览器打开 http://127.0.0.1:8899/ 验证三页
```

退出码：`0` 成功；`2` 配置缺失（无 Python/无 CLI/无 dist）；`3` 导出失败；`4` 发布失败。

---

## 4. 挂进每日计划任务（跑稳后启用）

`daily_incremental_collect.py` 已内置编排（2026-09-07），**默认关闭**：

```powershell
# 02:00 主任务命令追加 --publish-snapshot（重注册 register_local_collect_task.ps1
# 需同步修改其命令行；发布失败只记 ERROR，不影响采集退出码与 03:00 哨兵判定）
python scripts/ops/daily_incremental_collect.py --no-download --no-upload --lookback-days 7 --publish-snapshot
```

- 发布内部先导出后发布，导出失败不发布（`publish_static_snapshot.ps1` 保证）；
- 发布失败**不改变采集退出码**——采集结果判定与发布链路解耦（工程红线：任务必达优先）；
- 项目名默认 `EDGEONE_PAGES_PROJECT` env → `voc-platform`，可用 `--snapshot-project` 覆盖。

---

## 5. 本地验证记录（2026-09-07）

- 导出实测：383 路由 / 383 文件 / 5.4 MB（8 Steam + 5 B站）
- Playwright 静态站验证（http.server + 8899）：
  - **dashboard**：顶栏「静态快照 · 生成时间 · 库内评论 N 条」、KPI 3,917/80.2%、
    L1 备注、原声列表分页、导航只剩三页入口（admin/data 已裁剪）
  - **compare**：仅剩「累计」按钮（同期隐藏）、封面+发行日+评级、指标对比表全列有值
  - **bilibili**：视频下拉 5 个、hero 信息卡（播放/评论/弹幕/三连）、L1 正负双图、
    原声列表 likes 序、弹幕时间轴、高光时刻 LLM 总结
  - **降级**：未收录组合（如 `page=99`）→ `静态快照未收录该查询（/api/comments）`，页面 catch 展示
  - **窗口化**：`#/dashboard?target=…&range=30d` → label `2026-08-08 至 2026-09-06` + KPI 215 ✓
  - console 仅 favicon 404（无害）
- 回归：全量 pytest **138 例全绿**（新增 `tests/test_snapshot_export.py` 9 例）+ smoke 通过

---

## 6. 部署后开发差异（须知）

1. **改前端/聚合口径后**：重跑导出 + 发布，公网才更新；实时版不受影响；
2. **缓存串**：静态版 index.html 由导出脚本统一 stamp（`v=<快照日>`），与实时版 `v=20260906c` 互不干扰；
3. **新增筛选维度**：需先扩导出脚本的路由集合（并在 `tests/test_snapshot_export.py` 锁口径），否则公网 toast；
4. **schema/口径变更**：`service.py` 聚合函数改动会自动反映到下次快照——这也是「导出必须复用 service 层」的原因；
5. 快照是**公开产物**：任何新导出的端点都遵循决策 6 最小暴露原则（聚合 + 限量样本，无私密字段）。

---

## 7. 已知边界

- `custom` 自选时间、列表主题/情感筛选、翻页 >3 页、compare 同期口径 → 明确 toast 降级（设计如此，作品集场景够用）；
- 弹幕浮层样本为导出时刻的随机抽样（固定种子，同一快照内稳定）；
- 快照时间窗相对**导出日**固化，页面不重算相对日期——「近30天」= 快照生成日的近 30 天；
- EdgeOne Pages 免费额度对静态站足够；若未来要自定义域名/国内加速，在 Pages 控制台配域名即可；
- **部署域名默认带访问保护**（2026-09-07 实测）：CLI 输出的 Deploy URL 带 `eo_token/eo_time`
  签名参数，去掉签名返回 401——这是 EdgeOne 对部署预览 URL 的保护。**对外分享请用控制台
  「域名管理」里的正式生产域名**（或绑定自定义域名）；正式域名确认后更新本节。

---

## 📋 版本记录

---

## 📋 版本记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-09-07 | 初版：架构图 + 收敛策略 + 手动发布步骤 + 计划任务接入 + 本地验证记录 + 部署后差异；同日完成**首次公网发布**（项目 voc-platform，公网三页 Playwright 实测通过） | 方案③ 落地（工程师确认：只做③、EdgeOne Pages、三页、先手动后挂任务） |
