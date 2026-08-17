# 📦 产品文档索引（product/）

> **产品/设计/业务人员入口** — 不写给工程师看，写给"想理解产品在做什么、给谁做、做成什么样"的人看。

---

## 🗺️ 文档地图

```
product/
├── README.md                       ⬅ 你在这里：产品文档地图
├── overview.md                     产品概览（一句话定位 + 核心场景）
├── prototype/                      🎨 高保真原型
│   ├── voc-platform-prototype.html     v3 单文件自包含版（数据看板 + 原声列表 + 内嵌字体/logo）
│   └── src/                            v3 多文件源码（page.html / topbar.html / dashboard.* / voices.*）
├── prd/                            📋 产品需求文档（PRD）
│   (暂空，待 PRD 模板沉淀后启用)
└── decisions/                      📝 产品决策记录（ADR）
    (暂空，记录"为什么这么做"的决策历史)
```

---

## 📌 按"我想了解什么"快速定位

| 我想知道... | 看这里 |
|-------------|--------|
| **这个产品是干嘛的、解决什么问题** | [overview.md](./overview.md) |
| **仪表盘长什么样、怎么交互** | [prototype/voc-platform-prototype.html](./prototype/voc-platform-prototype.html) |
| **为什么选这个设计/方案** | `decisions/` （待沉淀） |
| **完整 PRD 长什么样** | `prd/` （待沉淀） |

---

## 🎯 与研发文档的区别

| 维度 | `docs/` | `product/` |
|------|---------|------------|
| **受众** | 工程师 | 产品/设计/业务 |
| **语言** | 技术术语 | 业务语言 |
| **内容** | 架构、数据、API、测试 | 场景、流程、原型、决策 |
| **示例** | "5 层数据架构" | "用户怎么看待仪表盘" |

---

## 🔗 关联

- **技术实现细节**：参见 [`docs/`](../docs/00-index.md)
- **项目门面**：参见 [`README.md`](../README.md)
