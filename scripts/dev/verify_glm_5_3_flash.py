"""一次性验证 glm-5.3-flash（主标注器）接通情况

2026-09-30 起 glm-5.3-flash 是**生产主标注器**，本脚本改为验证**生效配置**
（走 `get_analyzer()`，与主链路同一个入口），而不是自己拼环境变量：

1. `.env` 的 ANALYZER_PROVIDER 是否指向 glm-5.3-flash、Key / 端点是否可读到；
2. 端点 + model 是否与预期一致（套餐 key 必须走 Coding 专属端点）；
3. 是否能拿到合法的 AnalysisResult；
4. analyzer_version 是否为 llm:glm-5.3-flash@{hash8}。

用法：`.venv-ml\\Scripts\\python.exe scripts\\dev\\verify_glm_5_3_flash.py`
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from src.analyzers.base import get_analyzer  # noqa: E402

provider = (os.getenv("ANALYZER_PROVIDER") or "deepseek").lower()
base_url = os.getenv("GLM_5_3_FLASH_BASE_URL", "(未设置 → 走默认标准端点)")
model = os.getenv("GLM_5_3_FLASH_MODEL", "glm-5.3-flash")
print(f"ANALYZER_PROVIDER = {provider}")
print(f"base_url = {base_url}")
print(f"model    = {model}")
assert provider == "glm-5.3-flash", (
    f"当前主标注器是 {provider!r}，本脚本只验证 glm-5.3-flash —— "
    "请把 .env 的 ANALYZER_PROVIDER 设为 glm-5.3-flash"
)
assert "coding/paas" in base_url, (
    "GLM Coding Plan 套餐 key 必须走 Coding 专属端点 "
    "https://open.bigmodel.cn/api/coding/paas/v4/（标准端点会 429「余额不足或无可用资源包」）"
)

analyzer = get_analyzer()
print(f"\nanalyzer.provider = {getattr(analyzer, 'provider', '?')}")
print(f"analyzer.model = {analyzer.model}")
print(f"analyzer.client.base_url = {analyzer.client.base_url}")
print(f"analyzer.analyzer_version = {analyzer.analyzer_version}")

sample = "战斗手感不错但是优化太差了，30 系显卡都掉帧"
print(f"\n=== sample: {sample!r}")
result = analyzer.analyze_batch([sample], batch_size=1, raise_on_error=True)[0]
print(f"sentiment = {result.sentiment}")
print(f"sentiment_score = {result.sentiment_score}")
print(f"topic = {result.topic}")
for op in result.opinions:
    print(f"  opinion: [{op.sentiment}] {op.full_path} core={op.is_core} \"{op.phrase}\"")

assert result.opinions, "未产出任何观点 —— 检查 prompt/词典或端点连通性"
assert analyzer.analyzer_version.startswith("llm:glm-5.3-flash@"), analyzer.analyzer_version
print("\n[OK] glm-5.3-flash（主标注器）验证通过")
