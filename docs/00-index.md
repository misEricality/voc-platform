# 📚 研发文档索引（docs/）

> **入口文档** — 让任何工程师/产品/设计/测试同事能在 30 秒内找到需要的内容。

---

## 🗺️ 文档地图

```
docs/
├── 00-index.md             ⬅ 你在这里：文档地图
├── STEAM_API_FIELDS.md     🎮 Steam 官方 API 字段权威清单（实拉实测）
├── architecture/           🏗️ 架构设计（给工程师看的"系统是怎么搭的"）
│   ├── ANNOTATION_PIPELINE.md  方案4 标注流程（L1-L3 三级标签：观点短语→程序匹配）
│   ├── DATA_FIELDS.md          字段四级分类（A/B/C/D 前缀约定，D=本地模型派生向量）
│   ├── DATA_STORAGE_DESIGN.md  5 层数据架构 + 5 张表设计（含 comment_embeddings）
│   └── BILIBILI_COLLECTION.md  B 站采集规格（接口/数据映射/采样策略，开发交接文档）
├── plan/                   🗺️ 计划与里程碑
│   └── DEVELOPMENT_PLAN.md     完整路线图 + 优先级 + 决策依据
├── guides/                 📖 操作指南（"我该怎么用"）
│   └── QUICK_START.md          5 分钟上手指南
└── research/               🔍 调研资料
    └── VoC平台竞品调研报告.md     国际 + 国内主流 VoC 平台对比与启示
```

---

## 📌 按"我想做什么"快速定位

| 我想知道... | 看这里 |
|-------------|--------|
| **项目在做什么、做到哪了、下一步做什么** | [plan/DEVELOPMENT_PLAN.md](./plan/DEVELOPMENT_PLAN.md) |
| **5 分钟跑起来这个项目** | [guides/QUICK_START.md](./guides/QUICK_START.md) |
| **L1-L3 标注流程是怎么走的** | [architecture/ANNOTATION_PIPELINE.md](./architecture/ANNOTATION_PIPELINE.md) |
| **Steam API 能拿到哪些字段** | [STEAM_API_FIELDS.md](./STEAM_API_FIELDS.md) |
| **B 站采集怎么设计（接口/采样/落库）** | [architecture/BILIBILI_COLLECTION.md](./architecture/BILIBILI_COLLECTION.md) |
| **数据库有哪些字段、怎么命名的** | [architecture/DATA_FIELDS.md](./architecture/DATA_FIELDS.md) |
| **数据是怎么分层流转的、表怎么设计** | [architecture/DATA_STORAGE_DESIGN.md](./architecture/DATA_STORAGE_DESIGN.md) |
| **为什么选这个数据源、不选别的** | [research/VoC平台竞品调研报告.md](./research/VoC平台竞品调研报告.md) |
| **国内外 VoC 平台都在做什么** | [research/VoC平台竞品调研报告.md](./research/VoC平台竞品调研报告.md) |

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
| **重大决策记录** | 写入 `product/decisions/` （不在 docs/ 范围） |
| **调研资料归档** | 归 `research/` 子目录，命名用"<主题>调研报告.md" |

---

## 🔗 关联文档

- **产品/业务视角**：参见 [`product/README.md`](../product/README.md)
- **运维脚本**：参见 [`scripts/README.md`](../scripts/README.md)
- **数据探索（Jupyter）**：参见 [`notebooks/README.md`](../notebooks/README.md)
- **项目门面**：参见 [`README.md`](../README.md)