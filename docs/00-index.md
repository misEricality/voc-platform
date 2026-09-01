# 📚 研发文档索引（docs/）

> **入口文档** — 让任何工程师/产品/设计/测试同事能在 30 秒内找到需要的内容。

---

## 🗺️ 文档地图

```
docs/
├── 00-index.md             ⬅ 你在这里：文档地图
├── architecture/           🏗️ 架构设计（给工程师看的"系统是怎么搭的"）
│   ├── ANNOTATION_PIPELINE.md      方案4 标注流程（GDT v3.1.1：观点短语→程序匹配）
│   ├── AUTOMATION_PIPELINE.md      P6 自动化流水线落地架构（GH Release 累积 DB + 多目标驱动 + 故障排查）
│   ├── STEAM_API_FIELDS.md         🎮 Steam 官方 API 字段权威清单（实拉实测）
│   ├── DATA_FIELDS.md              字段四级分类（A/B/C/D 前缀约定，D=本地模型派生向量）
│   ├── DATA_STORAGE_DESIGN.md      数据分层架构 + 表设计（设计稿，含目标迁移路径）
│   ├── BILIBILI_COLLECTION.md      B 站采集规格（接口/数据映射/采样策略，开发交接文档）
│   ├── BILIBILI_AUTOMATION.md      B 站自动化采集设计（待采清单 + cron + CLI；2026-08-23 落地）
│   ├── DESIGN_TOKENS.md            设计 Token 规范 v1.0（色彩/字体/组件/图标/图表/双主题 + 三页迁移映射，前端执行依据）
│   ├── DUAL_ANNOTATION_QWEN_FLASH_2026-08-25_ARCHIVE.md  P11 双标注实验产物归档（QWEN-flash 404 失败案例）
│   └── SELF_HOSTED_VPS_DEPLOYMENT.md 形态 A 部署指南（公网可访问 / 数据全私有；Oracle Always Free + Caddy + Streamlit）
├── plan/                   🗺️ 计划与里程碑
│   ├── DEVELOPMENT_PLAN.md     完整路线图 + 优先级 + 决策依据
│   ├── P3_COMPARE_DESIGN.md    P3 多目标对比设计（数据/口径/图表选型/技术实现）
│   └── next-gen-tagging/       下一代标签系统（GDT+PEDM 双轨）分阶段采纳计划 + 原稿备份 + 验证报告
├── guides/                 📖 操作指南（"我该怎么用"）
│   ├── QUICK_START.md          5 分钟上手指南
│   ├── SETUP_ML_ENV.md         ML 环境搭建（torch + sentence-transformers）
│   └── PUSH_TROUBLESHOOTING.md 🛠️ Push 排查指南（决策树 + 已知坑 + 验证清单，每次 push 前必读）
└── research/               🔍 调研资料
    └── VOC_COMPETITOR_RESEARCH.md  国际 + 国内主流 VoC 平台对比与启示
```

---

## 📌 按"我想做什么"快速定位

| 我想知道... | 看这里 |
|-------------|--------|
| **项目在做什么、做到哪了、下一步做什么** | [plan/DEVELOPMENT_PLAN.md](./plan/DEVELOPMENT_PLAN.md) |
| **多目标对比视图怎么设计、怎么实现** | [plan/P3_COMPARE_DESIGN.md](./plan/P3_COMPARE_DESIGN.md) |
| **每天自动采集怎么跑、数据怎么累积下来** | [architecture/AUTOMATION_PIPELINE.md](./architecture/AUTOMATION_PIPELINE.md)（含 §8 决策与风险历史） |
| **标签/洞察体系未来怎么演进** | [plan/next-gen-tagging/ANNOTATION_SYSTEM_UPGRADE_PLAN.md](./plan/next-gen-tagging/ANNOTATION_SYSTEM_UPGRADE_PLAN.md) |
| **5 分钟跑起来这个项目** | [guides/QUICK_START.md](./guides/QUICK_START.md) |
| **本地 ML 环境怎么搭** | [guides/SETUP_ML_ENV.md](./guides/SETUP_ML_ENV.md) |
| **L1-L3 标注流程是怎么走的** | [architecture/ANNOTATION_PIPELINE.md](./architecture/ANNOTATION_PIPELINE.md) |
| **Steam API 能拿到哪些字段** | [architecture/STEAM_API_FIELDS.md](./architecture/STEAM_API_FIELDS.md) |
| **B 站采集怎么设计（接口/采样/落库）** | [architecture/BILIBILI_COLLECTION.md](./architecture/BILIBILI_COLLECTION.md) |
| **B 站自动化怎么跑（待采清单 + cron + CLI）** | [architecture/BILIBILI_AUTOMATION.md](./architecture/BILIBILI_AUTOMATION.md) |
| **怎么把仪表盘部署到公网，同时不让外人拿到数据** | [architecture/SELF_HOSTED_VPS_DEPLOYMENT.md](./architecture/SELF_HOSTED_VPS_DEPLOYMENT.md) |
| **怎么把代码推送到 GitHub（sandbox 推 vs 手动 git 推的决策树）** | [guides/PUSH_TROUBLESHOOTING.md](./guides/PUSH_TROUBLESHOOTING.md) |
| **前端页面的颜色/字体/组件/图表规范是什么** | [architecture/DESIGN_TOKENS.md](./architecture/DESIGN_TOKENS.md) |
| **数据库有哪些字段、怎么命名的** | [architecture/DATA_FIELDS.md](./architecture/DATA_FIELDS.md) |
| **数据是怎么分层流转的、表怎么设计** | [architecture/DATA_STORAGE_DESIGN.md](./architecture/DATA_STORAGE_DESIGN.md) |
| **为什么选这个数据源、不选别的** | [research/VOC_COMPETITOR_RESEARCH.md](./research/VOC_COMPETITOR_RESEARCH.md) |
| **国内外 VoC 平台都在做什么** | [research/VOC_COMPETITOR_RESEARCH.md](./research/VOC_COMPETITOR_RESEARCH.md) |
| **src/ 代码模块结构（pipeline / analyzers / collectors / queue / storage / visualizer 的职责）** | [../src/README.md](../src/README.md) |
| **tests/ 测试用例索引（49 例 pytest + 黄金集门禁 + ML skip）** | [../tests/README.md](../tests/README.md) |

---

## 🎯 文档受众对照

| 角色 | 推荐阅读顺序 |
|------|--------------|
| **新加入的工程师** | 00-index → QUICK_START → DEVELOPMENT_PLAN → DATA_STORAGE_DESIGN |
| **新加入的产品/设计** | 00-index → QUICK_START → 调研报告（业务部分） |
| **架构评审前** | DATA_FIELDS → DATA_STORAGE_DESIGN |
| **新版本规划前** | DEVELOPMENT_PLAN + 当前仪表盘截图 |
| **求职/作品展示** | README.md → 调研报告 → 仪表盘截图 |

---

## 📝 文档维护约定

| 规则 | 说明 |
|------|------|
| **A/B/C 字段前缀** | 与架构文档同步：原始 / 派生 / LLM 标注 |
| **里程碑编号 M1-M10** | 与 DEVELOPMENT_PLAN 一致，不重复定义 |
| **重大决策记录** | 沉淀在 `docs/plan/` 相关计划文档（如 DEVELOPMENT_PLAN 的"决策/复盘"小节、ANNOTATION_SYSTEM_UPGRADE_PLAN 的评审结论） |
| **调研资料归档** | 归 `research/` 子目录，命名用"<主题>调研报告.md" |

---

## 🔗 关联文档

- **产品/业务视角**：参见 [`product/README.md`](../product/README.md)
- **运维脚本**：参见 [`scripts/README.md`](../scripts/README.md)
- **数据探索**：当前无 notebook（`notebooks/` 已移除），ad-hoc 脚本见 `scripts/dev/`
- **项目门面**：参见 [`README.md`](../README.md)
