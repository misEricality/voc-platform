# 🎙️ VoC Platform · 消费者之声洞察平台

> **个人项目** —— 学习、探索、经验沉淀与作品展示  
> 调研报告见 [VoC平台竞品调研报告.md](./VoC平台竞品调研报告.md)

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-MVP-orange)

## ✨ 项目简介

VoC Platform 是一个面向个人开发者的**消费者之声（Voice of Customer）分析平台**，目标是用最低成本、最简单的架构，实现：

- 📥 **多平台数据采集**（Steam / B站 / 微博 / 京东等）
- 🤖 **AI 情感与主题分析**（DeepSeek / Qwen / GLM / 本地BERT）
- 📊 **可视化洞察仪表盘**（Streamlit + Plotly）
- ⏰ **自动化流水线**（GitHub Actions）

> 📌 **项目定位**：学习大模型API集成、NLP应用、数据可视化全链路；非商业产品。

## 🎯 当前进度（MVP 阶段一）

| 模块 | 状态 | 备注 |
|------|------|------|
| Steam 评测采集 | ✅ | 官方API，无需登录 |
| SQLite 存储 | ✅ | SQLAlchemy 2.x |
| LLM 情感分析 | ✅ | 支持 DeepSeek/Qwen/GLM 切换 |
| 本地BERT情感分析 | ✅ | 零成本备选 |
| Streamlit Dashboard | ✅ | 6个核心图表 |
| B站/微博采集 | 🚧 | 下一阶段 |
| 自动化流水线 | 🚧 | GitHub Actions |

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
├── README.md                 # 项目说明（你正在看的）
├── VoC平台竞品调研报告.md     # 完整调研报告
├── requirements.txt          # 依赖清单
├── .env.example              # 环境变量示例
├── .gitignore
├── app.py                    # Streamlit 仪表盘入口
├── src/
│   ├── collectors/           # 数据采集层
│   │   ├── base.py           # 抽象基类
│   │   └── steam.py          # Steam 采集器
│   ├── analyzers/            # 分析层
│   │   ├── base.py
│   │   ├── sentiment_llm.py  # LLM 情感分析
│   │   └── sentiment_local.py # 本地BERT
│   ├── storage/              # 存储层
│   │   └── db.py             # SQLite + ORM
│   ├── visualizer/           # 可视化
│   │   └── charts.py
│   └── pipeline.py           # 主流程编排
├── scripts/                  # 工具脚本
├── tests/                    # 单元测试
├── data/                     # 数据文件（gitignore）
├── docs/                     # 项目文档
└── notebooks/                # Jupyter 分析
```

## 💰 成本估算

| 场景 | 数据量 | 月成本 | 年成本 |
|------|--------|--------|--------|
| 仅采集（无分析） | 5000条/月 | ¥0 | ¥0 |
| DeepSeek 分析 | 5000条/月 | ¥3 | ¥36 |
| Qwen3-Flash 分析 | 5000条/月 | ¥1 | ¥12 |
| 本地BERT分析 | 任意 | ¥0 | ¥0 |

> 完全在 ¥100~1000/年 预算内。

## 🔌 支持的数据源

| 平台 | 状态 | 接入方式 | 合规性 |
|------|------|----------|--------|
| **Steam** | ✅ 已实现 | 官方 Web API | ★★★★★ |
| **B站** | 🚧 规划中 | 官方开放平台 | ★★★★ |
| **微博** | 🚧 规划中 | 官方 API + OAuth | ★★★★ |
| **京东** | 🚧 规划中 | 京东联盟 API | ★★★★★ |
| **小红书** | ⚠️ 暂缓 | 蒲公英合作申请 | ★★★ |

## ⚖️ 合规声明

> 本项目仅用于**学习研究目的**。  
> 所有采集的数据均为平台**公开可见**的内容。  
> 不存储任何用户隐私信息（手机号、地址、身份证等）。  
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

- [x] **v0.1 (当前)** - Steam 采集 + LLM 分析 + Streamlit Dashboard
- [ ] **v0.2** - B站视频评论接入 + 自动化（GitHub Actions）
- [ ] **v0.3** - 微博 + 多目标横向对比
- [ ] **v0.4** - 主题建模 + 趋势预警
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