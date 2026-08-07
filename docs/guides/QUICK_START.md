# 🚀 快速开始指南

> 5分钟从零跑起来

## 前置条件

- Python 3.10+
- pip

## 步骤 1：克隆仓库

```bash
git clone https://github.com/yourname/voc-platform.git
cd voc-platform
```

## 步骤 2：安装依赖

```bash
pip install -r requirements.txt
```

> 💡 **遇到 torch 安装慢？**  
> CPU 版本：`pip install torch --index-url https://download.pytorch.org/whl/cpu`

## 步骤 3：申请 API Key（仅 Steam 必填）

### 3.1 Steam Web API Key（必填）
1. 访问 https://steamcommunity.com/dev/apikey
2. 用 Steam 账号登录
3. 填写域名（个人项目填 `localhost` 或留空）
4. 获得 API Key

### 3.2 DeepSeek API Key（可选，用于AI分析）
1. 访问 https://platform.deepseek.com/
2. 注册并实名
3. 创建 API Key
4. 新用户赠送约 ¥10 余额（约 400万-1000万 tokens，足够个人项目用半年）

### 3.3 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：
```bash
STEAM_API_KEY=你的steam_key
DEEPSEEK_API_KEY=你的deepseek_key
ANALYZER_PROVIDER=deepseek
```

## 步骤 4：跑通第一个流程

### 4.1 仅采集（不需要 AI Key）

```bash
python -m src.pipeline --platform steam --target 730 --count 50 --skip-analysis
```

成功后会看到：
```
[INFO] 采集数据：platform=steam, target=730, count=50
[INFO]   采集到 50 条原始评论
[INFO] 写入数据库...
[INFO]   处理 50 条
```

### 4.2 完整流程（采集 + 分析）

```bash
python -m src.pipeline --platform steam --target 730 --count 50
```

### 4.3 查看结果

```bash
streamlit run app.py
```

浏览器访问 http://localhost:8501，看到仪表盘。

## 常见问题

### Q1：采集不到数据？
- 检查 Steam appid 是否正确（可在 Steam 商店URL看到）
- 改用 `--language english` 测试英文评测
- 检查网络是否能访问 store.steampowered.com

### Q2：AI 分析报错 "API Key 无效"？
- 检查 `.env` 文件中的 API Key 是否正确
- DeepSeek 注册地址：https://platform.deepseek.com/

### Q3：Streamlit 启动报错？
- 检查端口 8501 是否被占用：`streamlit run app.py --server.port 8502`
- 升级 streamlit：`pip install --upgrade streamlit`

### Q4：想换其他分析器？
编辑 `.env`：
```bash
ANALYZER_PROVIDER=qwen   # 或 glm、local
```

切换成本为 0，重新跑 pipeline 即可。

## 下一步

- 📖 阅读 [VoC平台竞品调研报告.md](../research/VoC平台竞品调研报告.md)
- 🛠️ 实现 B站/微博采集器
- ⏰ 配置 GitHub Actions 自动采集
- 📝 在掘金/CSDN 发布技术博客