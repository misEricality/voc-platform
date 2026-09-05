# 🎙️ 灵听 · Lynx · 消费者之声洞察平台

> **个人项目** —— 学习、探索、经验沉淀与作品展示  
> 完整调研：[docs/research/](./docs/research/) · 开发计划：[docs/plan/DEVELOPMENT_PLAN.md](./docs/plan/DEVELOPMENT_PLAN.md)

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-v0.7-blue)

## ✨ 项目简介

灵听 · Lynx（原 VoC Platform）是一个面向个人开发者的**消费者之声（Voice of Customer）分析平台**，目标是用最低成本、最简单的架构，实现：

- 📥 **多平台数据采集**（Steam / B站 / 微博 / 京东等）
- 🤖 **AI 情感与主题分析**（DeepSeek / Qwen / GLM / 本地BERT）
- 🔎 **语义向量化与检索**（本地 bge-small-zh，零 API 成本）
- 📊 **可视化洞察仪表盘**（Streamlit + Plotly）
- ⏰ **自动化流水线**（GitHub Actions）

> 📌 **项目定位**：学习大模型API集成、NLP应用、数据可视化全链路；非商业产品。

## 📍 项目状态速览（接手必读）

> 30 秒看完这个项目现在是什么 + 下一步是什么 + 终点是什么。

| 维度 | 当前（v0.7，2026-09-01 收口） |
|---|---|
| **✅ 已完成** | Steam 6 单机 + B 站 3 视频采集（14K 评论）；L1-L3 三级标签标注管线（方案4）；本地 bge 语义向量化；Streamlit 仪表盘（单目标 / 多目标对比 / 明细核查）；B 站单视频看板；**Web 实时看板**（FastAPI + 原生 SPA：主看板/游戏对比/B站视频/时间序列/系统管理 5 页，2026-09-02）；**P6 自动化流水线**（GH Actions daily cron + GH Release 累积 DB + CI pytest 护栏 + silent 失败防御）；**DESIGN_TOKENS v1.0**（三原型迁移版已上线） |
| **🔜 接下来（v1.0 前主线）** | **P9 阶段 2 L3.5 微话题聚类**（`l35_cluster.py` 骨架就绪）；**P9 阶段 3 PEDM 负向观点试点**（黄金集一致率 ≥80% 才放量）；**P11 QWEN-flash bogus 清理**（`reset_qwen_flash_bogus.py --commit`）；Web 看板 VPS 部署落地 |
| **🎯 最终目标（v1.0 作品集发布）** | 完整文档（README 进阶版）+ mermaid 架构图；1-2 篇复盘博客（踩坑 + 经验）；演示视频/GIF；求职作品集重点项目 |

→ 详细路线 / 当前主线 / 阻塞：[docs/plan/DEVELOPMENT_PLAN.md](./docs/plan/DEVELOPMENT_PLAN.md)
→ 主标注器：`glm-5.3-flash`（2026-08-31 切；与 DEEPSEEK/QWEN 共享 GDT v3.1.1 prompt 集合）
→ P6 daily cron：UTC 17:00（北京次日凌晨 1:00，避开 GH Actions schedule 最多 8h 延迟）

## 🎯 当前进度（v0.7 完成，v1.0 作品化进行中）

| 模块 | 状态 | 备注 |
|------|------|------|
| Steam 评测采集 | ✅ | 官方API + 翻页去重 + 验证页防漏采（主库聚焦 6 款单机；4 款网游 2026-08-23 归档） |
| **B站采集** | ✅ | 公开 Web 接口（免申请），7 天稳态快照 + 弹幕分片；3 视频 3030 评论已实测落库 |
| SQLite 存储 | ✅ | SQLAlchemy 2.x，冷启动 NULL + 7 天回采机制 |
| LLM 情感分析 | ✅ | **GLM-5.3-Flash 主标注器**（智谱 BigModel VLM，2026-08-31 切；与 DEEPSEEK/QWEN 共享 GDT v3.1.1 prompt 集合，不污染 `analyzer_version` 溯源；QWEN-flash 个人 token-plan 模型名 404，2026-08-25 回退） |
| **L1-L3 三级标签标注** | ✅ | 方案4：观点短语 → 程序匹配（GDT v3.1.1，L1 10 / L2 28 / L3 111） |
| **语义向量化** | ✅ | 本地 bge-small-zh（P2.5，零 API 成本，语义检索/聚类基建） |
| 本地BERT情感分析 | ✅ | 零成本备选 |
| **分析结果溯源** | ✅ | `comments.analyzer_version` 字段（`provider:model@prompt_hash8`），换模型/换 prompt 立刻可按 version 分组 |
| 高保真原型 v3 | ✅ | 数据看板 + 原声列表 + 游戏对比卡片页（单文件自包含：内嵌子集字体 + logo） |
| Streamlit Dashboard | ✅ | 单目标看板（6 图）+ 多目标对比视图（散点/堆叠/热力图/痛点/下钻）+ 📋 明细核查视图（多维筛选 + CSV 导出）|
| B 站单视频看板 | ✅ | v0.3 接 DB SPA（3 视频切换 + 4 区块：视频概览 / 评论情感与画像 / 主题情感分析 + 下钻 / 弹幕时间轴） |
| **Web 实时看板** | ✅ | FastAPI + 原生 ECharts SPA（2026-09-02）：主看板 / 游戏对比 / B站视频 / **时间序列（P8）** / 系统管理（管理员增删改采集任务）；实时读 `data/voc.db`；详见 [WEB_DASHBOARD.md](docs/architecture/WEB_DASHBOARD.md) |
| 微博采集 | 🚧 | 下一阶段 |
| 自动化流水线 | ✅ | P6 已落地：workflow cron + GH Release 累积 DB + CI pytest 护栏（test job）；P6 silent 失败防御上线（verify_release_upload.py）|

> 📊 **当前数据**（2026-09-01 收口，pending qwen-flash 261 条 bogus 清理）：**~14,478 条**评论（Steam 6 款单机 + 归档 4 款 + B 站 3 视频），观点级标注约 11,500 条，语义向量约 6,400 条（单模型 `bge-small-zh-v1.5`，已全量回填）。GDT v3.1.1 词典已扩充、兜底占比 topic 67.6% / opinion 67.4%，P3 多目标对比已上线，P6 自动化流水线每日增量入库（30 天回看 + bootstrap 累积 + 2026-08-31 smart_window v2「采昨天全天 + 前天全天」），P10 analyzer_version 字段已加（老数据 NULL = 未溯源，新数据自动写入）。DESIGN_TOKENS v1.0 已落地三原型 v2 迁移版。

## 🏷️ L1-L3 三级标签标注管线（方案4）

**LLM 自由提取观点短语 → 程序用定义词典匹配 L3 → 映射完整路径 L1/L2/L3。**

相比"LLM 强制枚举标签"，方案4 把选标签从 LLM 手里拿掉：多面评论（"打击感超爽但优化太差"）不再丢观点，匹配逻辑可控、可调、可测试。

- 标签体系：L1 10 类 / L2 28 类 / L3 111 类（GDT v3.1.1；`config/topics/gaming.yaml` + `l3_definitions.yaml`）
- 核心逻辑：`src/analyzers/normalize.py`（match_l3 / 词典索引 / 路径映射）
- 详细流程：[docs/architecture/ANNOTATION_PIPELINE.md](./docs/architecture/ANNOTATION_PIPELINE.md)

## 🚀 5分钟快速开始

> 💡 完整步骤见 [docs/guides/QUICK_START.md](./docs/guides/QUICK_START.md)（含 API Key 申请、常见问题 Q1-Q4、调试技巧）。
>
> 一句话版：
>
> ```bash
> git clone https://github.com/misEricality/voc-platform.git
> cd voc-platform
> pip install -r requirements.txt
> cp .env.example .env
> # 编辑 .env，填入 STEAM_API_KEY（必填）+ DEEPSEEK_API_KEY 或 GLM_API_KEY（可选）
> python -m src.pipeline --platform steam --target 730 --count 50 --skip-analysis
> streamlit run app.py  # 浏览器访问 http://localhost:8501
> ```
>
> 主标注器：GLM-5.3-Flash（2026-08-31 切换；`.env` 改 `ANALYZER_PROVIDER=glm-5.3-flash`）

## 📸 效果预览

仪表盘包含：
- 📊 情感分布饼图（好评/差评/中性占比）
- 🏷️ 主题分布 TOP10（如：性能、玩法、价格、客服）
- ☁️ 评论关键词词云
- 📈 情感分数直方图
- 💬 典型评论样本展示
- 📊 多目标对比视图（口碑散点 / 主题×游戏热力图 / 负面痛点 / 下钻）

## 🏗️ 项目结构

```
voc-platform/
├── AGENTS.md                        # 工程规范（Agent 必读）
├── README.md                       # 项目门面（本文件）
├── app.py                          # Streamlit 仪表盘入口
├── requirements.txt / .env.example
│
├── src/                            # 【核心代码】正式模块（5 子目录 + 1 顶层）
│   ├── pipeline.py                     主流程编排（CLI 入口 python -m src.pipeline）
│   ├── analyzers/                      分析器（情感 / 语义 / 标注）
│   │   ├── sentiment_llm.py               LLM 打标（4 个 provider：deepseek/qwen/glm/glm-5.3-flash）
│   │   ├── sentiment_local.py             本地 BERT 备选
│   │   ├── normalize.py                  L1-L3 三级标签匹配（match_l3 + GDT v3.1.1）
│   │   └── embedder.py                   本地 bge-small-zh 向量化
│   ├── collectors/                     采集器（Steam Web API + B站公开接口）
│   ├── storage/                        存储层（SQLAlchemy 2.x + SQLite 单库 4 表）
│   ├── queue/                          B 站采集队列（P5 自动化阶段 0，CLI：python -m src.queue add BV...）
│   └── visualizer/                     可视化图表
│
├── config/                          # 【业务配置】（与代码解耦）
│   ├── prompts/                        LLM prompt 模板（含 strict 收敛版）
│   ├── topics/                         三级标签体系 + L3 定义词典
│   └── monitoring/                     自动化采集目标清单（P6）
│
├── data/                            # 【运行时数据】（gitignore，不入库）
│   ├── voc.db                            SQLite 主库（2026-09-01 sync：~14,478 条评论）
│   ├── archive/                          4 款网游归档（2026-08-23）
│   └── exports/                          导出产物（gitignored）
│
├── docs/                            # 【研发文档】（技术视角）
│   ├── 00-index.md                      ⬅ 文档地图
│   ├── architecture/                    架构设计（12 文档，含 ANNOTATION/AUTOMATION/STEAM_API_FIELDS/BILIBILI/DESIGN_TOKENS/SELF_HOSTED_VPS/DEPLOYMENT_OPTIONS 等）
│   ├── guides/                          操作指南（QUICK_START / SETUP_ML_ENV / PUSH_TROUBLESHOOTING）
│   ├── plan/                            计划与里程碑（DEVELOPMENT_PLAN + next-gen-tagging/）
│   └── research/                        调研资料（VOC_COMPETITOR_RESEARCH）
│
├── product/                         # 【产品文档】（业务视角）
│   ├── README.md                        产品文档地图
│   ├── prototype_overview.md            界面原型交付概览
│   ├── export_bilibili_data.py          B 站单视频原型数据导出
│   ├── build_bilibili_video.py          B 站单视频原型 HTML 组装
│   └── prototype/                       高保真原型（v1 历史版 + v2 DESIGN_TOKENS 合规版 = 6 HTML）
│
├── scripts/                         # 【运维/开发脚本】
│   ├── README.md                        脚本索引
│   ├── smoke_test.py                    冒烟测试
│   ├── dev/                             开发期活跃脚本（11 个 + archive/ 42 个已归档）
│   └── ops/                             长期运维脚本（12 个：daily_incremental_collect / smart_sync_release / push_via_api 等）
│
├── tests/                           # 【测试】pytest 49 例（含黄金集回归 + ML 环境依赖时 skip）
│   ├── README.md                        测试索引
│   └── fixtures/                        黄金集 410 条 + 校正项
│
└── .workbuddy/                     # 本机跨会话记忆（gitignore，不入库）
    └── memory/                          MEMORY.md + YYYY-MM-DD.md
```

> 📌 **目录设计原则**：研发与产品文档分离 / 代码与配置分离 / 脚本分阶段 / 数据运行时不入库（gitignore）。  
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
- [x] **v0.4** - 词云 + 仪表盘洞察力增强 + 多目标横向对比（P3 已上线）
- [x] **v0.5** - B 站采集 + 多平台扩展（采集器 + 看板 v0.3）
- [x] **v0.6** - 自动化流水线 P6（GH Actions cron + GH Release 累积 DB）
- [x] **v0.7** - 分析结果溯源 P10（analyzer_version 字段）+ CI pytest 护栏 + P11 QWEN-flash bogus 清理工具 + DESIGN_TOKENS v1.0（三原型 v2 迁移版上线）
- [ ] **v1.0** - ✅ P8 时间序列已随 Web 看板交付（2026-09-02）→ P9 阶段 2/3 落地 + 完整文档 + 技术博客 + 求职作品集发布

## 📄 License

MIT License - 详见 [LICENSE](./LICENSE)

## 🙏 致谢

- [Steam Web API](https://steamcommunity.com/dev) - 公开评测数据源
- [智谱 BigModel](https://open.bigmodel.cn/) - **GLM-5.3-Flash（2026-08-31 起主标注器）**
- [DeepSeek](https://platform.deepseek.com/) - 备选 LLM 标注器
- [Streamlit](https://streamlit.io/) - 快速构建数据应用

---

## 👤 作者

**EricChan** · 项目设计 + 实现 + 维护

- **GitHub**: [@misEricality](https://github.com/misEricality)
- **项目主页**: [voc-platform](https://github.com/misEricality/voc-platform)
- **问题反馈 / 商业合作**: [GitHub Issues](https://github.com/misEricality/voc-platform/issues)
- **创作周期**: 2026-07 ~ 至今（v0.7 收口 → v1.0 作品化进行中）
- **定位**: 个人学习作品集 / 求职展示 / 大模型 API + NLP 全链路实战

> 📌 邮箱 / 微信等私下联系方式不接受直接索取（防骚扰）。如需商业合作 / 学术引用 / 投稿需求，请走 [GitHub Issues](https://github.com/misEricality/voc-platform/issues/new) 走公开流程。
> 学术引用格式见 [`CITATION.cff`](./CITATION.cff)（GitHub 「Cite this repository」按钮自动识别）。

⭐ **如果这个项目对你有帮助，欢迎 Star！**

📮 **问题反馈**：通过 GitHub Issues
