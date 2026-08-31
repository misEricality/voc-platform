"""一次性验证 glm-5.3-flash provider 接通情况

不联网 Steam，只调一次 analyzer 看：
1. API key 是否能从环境变量拿到
2. 端点 + model 是否正确
3. 是否能拿到合法的 AnalysisResult
4. analyzer_version 是否 = llm:glm-5.3-flash@{hash8}
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

# 验证 key 可达
api_key = os.getenv("GLM_API_VOC_PLATFORM")
base_url = os.getenv("GLM_5_3_FLASH_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
model = os.getenv("GLM_5_3_FLASH_MODEL", "glm-5.3-flash")
print(f"key 可达: {bool(api_key)} (length={len(api_key) if api_key else 0})")
print(f"base_url = {base_url}")
print(f"model = {model}")
assert api_key, "GLM_API_VOC_PLATFORM 为空 —— 检查用户变量 glm_api_voc_platform 是否存在"

# 初始化 analyzer
from src.analyzers.sentiment_llm import LLMSentimentAnalyzer
analyzer = LLMSentimentAnalyzer(provider="glm-5.3-flash", topic_category="gaming")
print(f"\nanalyzer.provider = {analyzer.provider}")
print(f"analyzer.model = {analyzer.model}")
print(f"analyzer.client.base_url = {analyzer.client.base_url}")
print(f"analyzer.analyzer_version = {analyzer.analyzer_version}")

# 跑一条真实评论
sample = "战斗手感不错但是优化太差了，30 系显卡都掉帧"
print(f"\n=== sample: {sample!r}")
result = analyzer.analyze(sample)
print(f"sentiment = {result.sentiment}")
print(f"sentiment_score = {result.sentiment_score}")
print(f"topic = {result.topic}")
print(f"opinions = {result.opinions}")
print(f"\n[OK] glm-5.3-flash 验证通过")