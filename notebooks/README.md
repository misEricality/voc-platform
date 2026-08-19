# 📓 数据探索（notebooks/）

> **Jupyter Notebooks 存放目录** — 用于 ad-hoc 数据探索、可视化原型、模型调试。

---

## 📌 当前状态

**已到启用门槛（2026-08-19）**：评论数据已达 **9308 条**（Steam 8302 + B站 1006，已分析 9242），观点级标注 15298 条，语义向量 9309 条（全量回填）。

适合用 Jupyter 的场景（按优先级）：
- 「总体体验评价」兜底桶承载仍偏重（2026-08-19 词典扩充+重匹配后：观点级 67.4%、topic 67.6%；剩余为数据集固有纯整体褒贬）→ 若要继续，转向数据侧采更多含具体维度的长评，或用 L3.5 聚类挖掘长尾
- 跨平台（Steam vs B站）情感 / 主题对比探索
- 弹幕语义分析（B站独有资产，带视频时间轴）

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
