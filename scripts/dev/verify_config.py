"""验证：配置外部化后 prompt 与主题词表能正确加载"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.analyzers.sentiment_llm import (
    _load_prompt,
    _load_topic_config,
    build_user_prompt,
)

# 1. 加载 prompt
system_prompt = _load_prompt("sentiment.txt")
user_template = _load_prompt("sentiment_user.txt")
print(f"[1] system prompt: {len(system_prompt)} chars loaded")
print(f"[2] user template: {len(user_template)} chars loaded")

# 2. 加载主题词表
topic_cfg = _load_topic_config("gaming")
print(f"[3] topic config: primary={len(topic_cfg['primary'])} items, fallback={topic_cfg['fallback']!r}")
assert len(topic_cfg["primary"]) == 10, "v0.1 应保留 10 个主题词（不修改默认值）"

# 3. 构造最终 user prompt（验证主题词表已注入）
final = build_user_prompt("测试评论文本", None, topic_cfg["primary"])
assert "游戏性/性能/价格" in final, "主题词表应注入到 user prompt"
print(f"[4] final prompt: {len(final)} chars, 主题词表注入 OK")
print()

print("=== 配置外部化验证通过 ===")
print("下一步：实际跑一次 pipeline 确认 LLM 输出未变化。")