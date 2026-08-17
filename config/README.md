# ⚙️ 业务配置（config/）

> **业务知识与代码解耦** — 改业务配置不必动 Python 代码，不必重启服务。

---

## 🗺️ 配置地图

```
config/
├── README.md                       ⬅ 你在这里
├── prompts/                        🤖 LLM prompt 模板
│   ├── sentiment.txt                   情感分析系统提示词
│   ├── sentiment_user.txt              情感分析用户提示词模板（观点短语提取，含占位符）
│   └── sentiment_user_strict.txt       收敛轮严格版（强制 ≥1 条观点，方案4 第 2/3 轮）
└── topics/                         🏷️ 三级标签体系
    ├── gaming.yaml                     GDT v3.1.1：L1 10 / L2 28 / L3 111 三级标签树
    └── l3_definitions.yaml             111 个 L3 定义 + 关键词（程序匹配词典）
```

---

## 🔌 运行时配置（.env，不在 config/ 目录）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | 语义向量化模型。换模型 = 跑 `scripts/ops/backfill_embeddings.py --force` 全量重算（衍生数据可重建，详见该脚本注释） |

> embedding 模型属于**运行时环境**（依赖本地缓存与算力），故放 `.env` 而非 `config/`；
> 标签体系、prompt 等**业务知识**仍留在 `config/`。

---

## 📌 各配置用途

| 配置 | 何时编辑 | 影响范围 |
|------|----------|----------|
| **prompts/sentiment.txt** | 想换 LLM 分析风格 | 全部分析器输出格式 |
| **prompts/sentiment_user.txt** | 想调整观点提取 / 评分标准 / 输出字段 | 分析器的 opinions 输出 |
| **prompts/sentiment_user_strict.txt** | 想调整收敛轮强制规则 | 方案4 第 2/3 轮重打 |
| **topics/gaming.yaml** | 想增/改/删 L1-L3 标签树 | 标签体系（需同步 l3_definitions） |
| **topics/l3_definitions.yaml** | 想调关键词匹配词典（如新增 CS2 黑话） | `normalize.match_l3` 匹配结果 |

---

## 🎯 与代码的边界

| 关注点 | 在哪里 | 谁来改 |
|--------|--------|--------|
| **业务逻辑**（评分阈值、分类标准） | `config/` | 产品 / 业务 |
| **代码逻辑**（如何调 LLM、如何存 DB） | `src/` | 工程师 |
| **运行时数据** | `data/` | 系统生成 |

> ✅ 改 `config/` 文件不需要重启 Python 进程（按需 reload）。
> ⚠️ 不要把敏感信息（API Key、密码）写进 `config/`，统一放 `.env`。

---

## 📝 配置格式约定

| 文件类型 | 格式 | 适合内容 |
|----------|------|----------|
| `.txt` | 纯文本 | LLM prompt（含 `{}` 占位符） |
| `.yaml` | YAML | 结构化清单（主题词表、目标清单） |
| `.json` | JSON | 复杂嵌套配置（暂未使用） |

---

## ⚠️ 不要做的事

- ❌ 在 prompt 里写绝对路径或 API Key
- ❌ 让 config 文件夹超 50 个文件（应分层到子目录）
- ❌ 把 config 改动写进 git commit message 当作"代码变更"
- ❌ 用 config 替代代码（复杂判断逻辑应进 `src/`）
