"""对照实验：同一模板 循环3次 vs 批量3条（确定批量是否损坏质量）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from dotenv import load_dotenv
load_dotenv()

from src.analyzers.sentiment_llm import LLMSentimentAnalyzer
from src.analyzers.normalize import format_l3_full

analyzer = LLMSentimentAnalyzer('deepseek')
samples = [
    '和朋友开黑最快乐，天天五排上分',
    '打击感超爽但优化太差，卡成PPT，闪退好几次',
    '游戏神作但价格太贵',
]

# A. 循环单条（复用 analyze 内部批量逻辑但每条单独调用）
print('=== A. 循环单条 ===')
for s in samples:
    r = analyzer.analyze(s)
    print(f'  {s[:30]} -> opinions={len(r.opinions)} topic={r.topic}')

# B. 批量 3 条
print('=== B. 批量 3 条 ===')
for s, r in zip(samples, analyzer.analyze_batch(samples, batch_size=3)):
    print(f'  {s[:30]} -> opinions={len(r.opinions)} topic={r.topic}')
