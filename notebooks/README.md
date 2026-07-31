# 📓 数据探索（notebooks/）

> **Jupyter Notebooks 存放目录** — 用于 ad-hoc 数据探索、可视化原型、模型调试。

---

## 📌 当前状态

**暂未启用**。当前数据量（50 条）不需要 Jupyter 分析。

预计在以下情况启用：
- 数据量达 1000+ 条，需要探索性分析
- 主题聚类模型调试（embeddings 可视化、降维）
- 仪表盘新增图表前的原型验证

---

## 🗺️ 预期结构

```
notebooks/
├── README.md
├── exploratory/                    探索性分析（一次性）
│   └── 2026-XX-XX-topic-clustering.ipynb
├── model_debug/                    模型调试
│   └── sentiment-few-shot-experiment.ipynb
└── prototype/                      仪表盘原型
    └── wordcloud-variants.ipynb
```

---

## ⚠️ 约束

- **不要 commit 大数据快照**：>1MB 的 CSV / Parquet 文件应放 `data/`
- **Notebook 输出清理后再提交**：避免 commit 内含大量 print 输出
- **结论写进 Markdown 文档**：notebook 跑完的洞察应整理到 `docs/research/` 或 `product/decisions/`