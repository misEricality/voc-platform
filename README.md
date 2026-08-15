# 🎙️ VoC Platform · 消费者之声洞察平台

> **个人项目** —— 学习、探索、经验沉淀与作品展示  
> 完整调研：[docs/research/](./docs/research/) · 开发计划：[docs/plan/DEVELOPMENT_PLAN.md](./docs/plan/DEVELOPMENT_PLAN.md)

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-v0.2-blue)

## ✨ 项目简介

VoC Platform 是一个面向个人开发者的**消费者之声（Voice of Customer）分析平台**，目标是用最低成本、最简单的架构，实现：

- 📥 **多平台数据采集**（Steam / B站 / 微博 / 京东等）
- 🤖 **AI 情感与主题分析**（DeepSeek / Qwen / GLM / 本地BERT）
- 🔎 **语义向量化与检索**（本地 bge-small-zh，零 API 成本）
- 📊 **可视化洞察仪表盘**（Streamlit + Plotly）
- ⏰ **自动化流水线**（GitHub Actions）

> 📌 **项目定位**：学习大模型API集成、NLP应用、数据可视化全链路；非商业产品。

## 🎯 当前进度（v0.2 → v0.3）

| 模块 | 状态 | 备注 |
|------|------|------|
| Steam 评测采集 | ✅ | 官方API + 翻页去重 + 验证页防漏采 |
| **B站采集** | ✅ | 公开 Web 接口（免申请），7 天稳态快照 + 弹幕分片，已实测落库 |
| SQLite 存储 | ✅ | SQLAlchemy 2.x，冷启动 NULL + 7 天回采机制 |
| LLM 情感分析 | ✅ | 支持 DeepSeek/Qwen/GLM 切换 |
| **L1-L3 三级标签标注** | ✅ | 方案4：观点短语 → 程序匹配（L1 7 / L2 28 / L3 128） |
| **语义向量化** | ✅ | 本地 bge-small-zh（P2.5，零 API 成本，语义检索/聚类基建） |
| 本地BERT情感分析 | ✅ | 零成本备选 |
| 高保真原型 v2 | ✅ | 数据看板 + 原声列表（多文件拆分） |
| Streamlit Dashboard | ✅ | 6个核心图表 |
| 微博采集 | 🚧 | 下一阶段 |
| 自动化流水线 | 🚧 | workflow 已在远端；定时任务数据持久化方案待设计（当前每日全新库，不累积） |

> 📊 **当前数据**（2026-08-15）：**3073 条**评论（Steam 2067 + B站 1006，已分析 3034），观点级标注 5212 条，语义向量 1021 条（单模型 `bge-small-zh-v1.5`，覆盖约 1/3 待回填）。

## 🏷️ L1-L3 三级标签标注管线（方案4）

**LLM 自由提取观点短语 → 程序用定义词典匹配 L3 → 映射完整路径 L1/L2/L3。**

相比"LLM 强制枚举标签"，方案4 把选标签从 LLM 手里拿掉：多面评论（"打击感超爽但优化太差"）不再丢观点，匹配逻辑可控、可调、可测试。

- 标签体系：L1 7 类 / L2 28 类 / L3 128 类（`config/topics/gaming.yaml` + `l3_definitions.yaml`）
- 核心逻辑：`src/analyzers/normalize.py`（match_l3 / 词典索引 / 路径映射）
- 详细流程：[docs/architecture/ANNOTATION_PIPELINE.md](./docs/architecture/ANNOTATION_PIPELINE.md)

## 🚀 5分钟快速开始

### 1. 克隆并安装依赖

```bash
git clone https://github.com/yourname/voc-platform.git
cd voc-platform
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入以下至少一个：
# - STEAM_API_KEY（申请：https://steamcommunity.com/dev/apikey）
# - DEEPSEEK_API_KEY（申请：https://platform.deepseek.com/）
```

### 3. 第一次采集与分析

```bash
# 采集 CS2 的中文评测（无需 API Key，仅采集）
python -m src.pipeline --platform steam --target 730 --count 50 --skip-analysis

# 启用情感分析（需要配置 DeepSeek Key）
python -m src.pipeline --platform steam --target 730 --count 50
```

### 4. 启动可视化仪表盘

```bash
streamlit run app.py
# 浏览器访问 http://localhost:8501
```

## 📸 效果预览

仪表盘包含：
- 📊 情感分布饼图（好评/差评/中性占比）
- 🏷️ 主题分布 TOP10（如：性能、玩法、价格、客服）
- ☁️ 评论关键词词云
- 📈 情感分数直方图
- 💬 典型评论样本展示

## 🏗️ 项目结构

```
voc-platform/
├── README.md                       # 项目门面
├── app.py                          # Streamlit 仪表盘入口
├── requirements.txt / .env.example
│
├── src/                            # 【核心代码】
│   ├── collectors/                     数据采集器（Steam 官方 API + B站公开接口）
│   ├── analyzers/                      分析器（LLM 打标 + 程序匹配 L3 + 向量化）
│   │   ├── normalize.py                L3 匹配层（match_l3 / 词典索引 / 路径映射）
│   │   └── embedder.py                 本地语义向量（bge-small-zh，语义检索/聚类）
│   ├── storage/                        存储层（comments + comment_opinions + comment_embeddings 三表）
│   ├── visualizer/                     可视化图表
│   └── pipeline.py                     主流程编排（采集→入库→向量化→打标）
│
├── config/                          # 【业务配置】（与代码解耦）
│   ├── prompts/                        LLM prompt 模板（含 strict 收敛版）
│   └── topics/                         三级标签体系 + L3 定义词典
│
├── data/                            # 【运行时数据】（gitignore，不入库）
│   ├── voc.db                            SQLite 单库（3034 条已标注）
│   └── exports/                          导出产物（xlsx / csv / json）
│
├── docs/                            # 【研发文档】（技术视角）
│   ├── 00-index.md                      ⬅ 文档地图
│   ├── architecture/                    架构设计（含 ANNOTATION_PIPELINE 标注流程）
│   ├── STEAM_API_FIELDS.md              Steam API 字段权威清单
│   ├── plan/                            计划与里程碑
│   ├── guides/                          操作指南
│   └── research/                        调研资料
│
├── product/                         # 【产品文档】（业务视角）
│   ├── overview.md                      产品概览
│   ├── prototype/                       高保真原型（v2 多文件拆分）
│   ├── prd/                             需求文档（预留）
│   └── decisions/                       决策记录（预留）
│
├── scripts/                         # 【运维/开发脚本】
│   ├── smoke_test.py                    冒烟测试
│   ├── dev/                             开发期一次性脚本（采集/标注/巡检/E2E）
│   └── ops/                             refresh_likes 回采 / backfill_embeddings 向量回填
│
├── tests/                           # 【测试】pytest 10 例（9 通过 + 1 环境依赖跳过）
│
└── notebooks/                       # 【数据探索】（预留）
```

> 📌 **目录设计原则**：研发与产品文档分离 / 代码与配置分离 / 脚本分阶段 / 数据分层映射目录。  
> 📖 详细索引：[docs/00-index.md](./docs/00-index.md) · [product/README.md](./product/README.md)

## 💰 成本估算

| 场景 | 数据量 | 月成本 | 年成本 |
|------|--------|--------|--------|
| 仅采集（无分析） | 5000条/月 | ¥0 | ¥0 |
| DeepSeek 分析 | 5000条/月 | ¥3 | ¥36 |
| Qwen3-Flash 分析 | 5000条/月 | ¥1 | ¥12 |
| 本地BERT分析 | 任意 | ¥0 | ¥0 |
| 本地语义向量化（bge-small-zh） | 任意 | ¥0 | ¥0 |

> 完全在 ¥100~1000/年 预算内。向量化走本地模型，不产生 API 费用；首次需下载模型（约 95MB）。

## 🔌 支持的数据源

| 平台 | 状态 | 接入方式 | 合规性 |
|------|------|----------|--------|
| **Steam** | ✅ 已实现 | 官方 Web API | ★★★★★ |
| **B站** | ✅ 已实现 | 公开 Web 接口（免申请，需 buvid + 浏览器头；热门视频评论需 SESSDATA cookie） | ★★★★ |
| **微博** | 🚧 规划中 | 官方 API + OAuth | ★★★★ |
| **京东** | 🚧 规划中 | 京东联盟 API | ★★★★★ |
| **小红书** | ⚠️ 暂缓 | 蒲公英合作申请 | ★★★ |

## ⚖️ 合规声明

> 本项目仅用于**学习研究目的**。  
> 所有采集的数据均为平台**公开可见**的内容。  
> 不存储任何敏感个人信息（手机号、地址、身份证等）；仅保留公开可见的伪匿名标识（平台匿名 ID、昵称等）。  
> 不进行大规模商业化采集。  
> 使用前请阅读各平台的开发者协议与 robots.txt。

## 📚 学习价值

完成本项目后，你将掌握：

- ✅ 多源异构数据采集（HTTP API / 浏览器自动化）
- ✅ 大模型 API 集成与 Prompt 工程
- ✅ 中文 NLP 实战（情感分析、主题分类、关键词提取）
- ✅ 数据可视化（Streamlit / Plotly / 词云）
- ✅ 自动化流水线（GitHub Actions）
- ✅ 数据库设计与 ORM 最佳实践

## 🗺️ 路线图

- [x] **v0.1** - Steam 采集 + LLM 分析 + Streamlit Dashboard
- [x] **v0.2** - 采集生命周期管理（7 天回采）+ 6 款游戏批量采集 + L1-L3 标注管线（方案4）
- [x] **v0.3** - 语义向量化基础层（P2.5）+ 主题分类精细（L1-L3 三级标签）
- [ ] **v0.4** - 词云 + 仪表盘洞察力增强 + 多目标横向对比
- [ ] **v0.5** - 自动化流水线（CI 远端就绪）+ 微博接入（B站已先行落地）
- [ ] **v1.0** - 完整文档 + 技术博客 + 求职作品集发布

## 📄 License

MIT License - 详见 [LICENSE](./LICENSE)

## 🙏 致谢

- [Steam Web API](https://steamcommunity.com/dev) - 公开评测数据源
- [DeepSeek](https://platform.deepseek.com/) - 性价比最高的大模型API
- [Streamlit](https://streamlit.io/) - 快速构建数据应用

---

⭐ **如果这个项目对你有帮助，欢迎 Star！**

📮 **问题反馈**：通过 GitHub Issues