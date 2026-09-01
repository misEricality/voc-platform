# 📦 核心代码库（src/）

> **正式模块，不是脚本。** 所有跨脚本复用的代码都在这里；一次性逻辑走 `scripts/`。
>
> **最后更新**：2026-09-01（HANDOVER 收口：补 src/README.md · 模块职责索引）

---

## 🗺️ 模块地图

```
src/
├── README.md                  ⬅ 你在这里：src/ 模块职责索引
├── __init__.py                包标记（空文件）
├── pipeline.py                🚀 主流程编排（CLI 入口 `python -m src.pipeline`）
├── analyzers/                 🤖 分析器（情感 / 语义 / 标注）
├── collectors/                📥 采集器（多平台数据源）
├── queue/                     📋 B 站采集队列（P5 自动化阶段 0）
├── storage/                   💾 存储层（SQLAlchemy 2.x + SQLite）
└── visualizer/                📊 可视化图表（Streamlit 调用）
```

---

## 顶层文件

### `pipeline.py` · 主流程编排（CLI 入口）

| 项 | 值 |
|---|---|
| **职责** | 串联采集 → 入库 → 向量化 → 打标 → 回写全流程；CLI 入口 `python -m src.pipeline --platform steam --target 730 --count 50 [--skip-analysis]` |
| **更新** | 2026-08-28（P9 阶段 2 / P6 daily 入口衔接） |
| **依赖** | collectors / storage / analyzers / visualizer 全套 |

### `__init__.py`

| 项 | 值 |
|---|---|
| **职责** | 包标记（Python 模块必需；当前为空文件） |
| **更新** | 2026-08-01（仓库初始化时建立） |

---

## `analyzers/` · 分析器（情感 / 语义 / 标注）

**职责定位**：把原始评论文本转为结构化信号（情感 + 观点 + 标签 + 向量）。所有 analyzer 都暴露 `analyzer_version` 属性（P10 溯源）。

| 文件 | 职责 | 更新 |
|---|---|---|
| `__init__.py` | 包标记（空文件） | 2026-08-01 |
| `base.py` | 分析器抽象基类（统一 `analyzer_version` 接口契约） | 2026-08-06 |
| `embedder.py` | 本地 **bge-small-zh-v1.5** 语义向量化（512 维，零 API 成本，单例加载 + `semantic_search` 接口） | 2026-08-11 |
| `normalize.py` | **L1-L3 三级标签匹配核心**（GDT v3.1.1：`match_l3()` 五级规则 + 词典索引 + 路径映射；程序匹配层，非 LLM 选标签） | 2026-08-19 |
| `sentiment_llm.py` | **LLM 打标器**（支持 deepseek / qwen / glm / glm-5.3-Flash 4 个 provider；`analyzer_version = llm:{model}@{prompt_hash8}`） | 2026-08-31（GLM-5.3-Flash 默认标注器切换） |
| `sentiment_local.py` | 本地 BERT 情感分析（零成本备选；无 ML 环境时跳过） | 2026-08-21 |

> 📖 标注流程详见 [docs/architecture/ANNOTATION_PIPELINE.md](../docs/architecture/ANNOTATION_PIPELINE.md)

---

## `collectors/` · 采集器（多平台数据源）

**职责定位**：从外部平台拉评论 / 弹幕原始数据，统一为 `RawComment` 数据模型落库。

| 文件 | 职责 | 更新 |
|---|---|---|
| `__init__.py` | 包标记（空文件） | 2026-08-01 |
| `base.py` | 采集器抽象基类（`RawComment` 数据模型定义 + `fetch_metadata` 开关） | 2026-08-19 |
| `steam.py` | **Steam Web API 采集器**（`filter` 参数 + 翻页去重 `seen_source_ids` + 应用层时间窗过滤 + 7 天回采开关） | 2026-08-19 |
| `bilibili.py` | **B 站公开 Web 接口采集器**（评论 + 弹幕；buvid + 风控适配；7 天稳态快照 + 阈值分支 T=2,000 / K=1,000 + 弹幕分片 ≤3,000） | 2026-08-20 |

> 📖 采集规格详见：
> - Steam 字段字典：[docs/STEAM_API_FIELDS.md](../docs/STEAM_API_FIELDS.md)
> - B 站规格：[docs/architecture/BILIBILI_COLLECTION.md](../docs/architecture/BILIBILI_COLLECTION.md)

---

## `queue/` · B 站采集队列（P5 自动化）

**职责定位**：工程师手输 BV 号到 `bilibili_queue` 表，系统识别投稿时间 + 自动计算第 7 天 + 每日 cron 触发采集。状态机：`pending → scheduled → running → completed / failed`。

| 文件 | 职责 | 更新 |
|---|---|---|
| `__init__.py` | 包标记（暴露 cli / runner 给 `__main__` 调用） | 2026-08-23 |
| `__main__.py` | 包入口（让 `python -m src.queue ...` 工作） | 2026-08-23 |
| `cli.py` | **CLI 子命令**（add / list / due / run-due / skip / remove / show 共 7 个子命令） | 2026-08-23 |
| `runner.py` | **调度逻辑**（扫 `bilibili_queue` 表里 due 的视频 → 触发 `pipeline.run_pipeline` 采集） | 2026-08-23 |

> 📖 自动化设计详见 [docs/architecture/BILIBILI_AUTOMATION.md](../docs/architecture/BILIBILI_AUTOMATION.md)

---

## `storage/` · 存储层（SQLAlchemy 2.x + SQLite）

**职责定位**：单库 `data/voc.db` + 4 张业务表（comments / comment_opinions / comment_embeddings / danmaku）+ 仓储方法（聚合查询 / upsert / list_targets / 等）。P10 init_db 内置轻量 schema 演进（nullable 列自动 ALTER）。

| 文件 | 职责 | 更新 |
|---|---|---|
| `__init__.py` | 包标记（空文件） | 2026-08-01 |
| `db.py` | **SQLAlchemy 2.x ORM + 仓储方法**（4 表 Model + 评论/观点/向量/弹幕 CRUD + 多目标对比聚合查询 `list_targets` / `opinion_matrix` / `sentiment_ratio` / `negative_pain_points` 等） | 2026-08-23 |

> 📖 数据分层与表设计详见 [docs/architecture/DATA_STORAGE_DESIGN.md](../docs/architecture/DATA_STORAGE_DESIGN.md)
> 📖 字段字典（ABCD 四级前缀）详见 [docs/architecture/DATA_FIELDS.md](../docs/architecture/DATA_FIELDS.md)

---

## `visualizer/` · 可视化图表（Streamlit 调用）

**职责定位**：供 `app.py` 调用的 Plotly 图表函数库；不依赖 Streamlit 本身，可独立 import。

| 文件 | 职责 | 更新 |
|---|---|---|
| `__init__.py` | 包标记（空文件） | 2026-08-01 |
| `charts.py` | **Plotly 图表函数**（口碑散点 / 情感 100% 堆叠条 / 主题×游戏热力图 / 负面痛点 TOP5 / 主题分布饼图 / 词云 等）；P3 多目标对比视图所有图表 | 2026-08-19 |

---

## 🔗 跨模块调用关系

```
pipeline.py
  ├→ collectors/steam.py      → src/storage/db.py
  ├→ collectors/bilibili.py   → src/storage/db.py
  ├→ analyzers/sentiment_llm.py  → src/storage/db.py
  ├→ analyzers/sentiment_local.py → src/storage/db.py
  ├→ analyzers/embedder.py    → src/storage/db.py
  └→ analyzers/normalize.py   → (read comment_opinions)

queue/runner.py
  └→ pipeline.py              (采集流程复用)

app.py (Streamlit)
  ├→ storage/db.py            (数据读取)
  └→ visualizer/charts.py     (图表渲染)
```

> 💡 `src/` 是**正式模块**——任何新增可复用代码都应放这里，不要写到 `scripts/`。
