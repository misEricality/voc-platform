"""本地开源模型的情感分析器（备用）

完全免费，零API成本，但需要本机有推理能力。
- 默认模型：uer/roberta-base-finetuned-dianping-chinese
- 适用场景：大批量数据、无网络/无API Key时

依赖：pip install transformers torch
"""

from __future__ import annotations

from .base import BaseAnalyzer, AnalysisResult


class LocalSentimentAnalyzer(BaseAnalyzer):
    """基于 HuggingFace 中文情感分类模型的本地分析器"""

    name = "local"

    DEFAULT_MODEL = "uer/roberta-base-finetuned-dianping-chinese"

    @property
    def analyzer_version(self) -> str:
        """分析溯源标识：'{name}:{model_name}@local'（无 prompt 概念，故后缀为 ``local``）"""
        return f"{self.name}:{self.model_name}@local"

    def __init__(self, model_name: str | None = None, **kwargs):
        super().__init__(**kwargs)
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        import torch

        self.model_name = model_name or self.DEFAULT_MODEL
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        self.model.eval()

    def analyze(self, text: str, *, context: dict | None = None) -> AnalysisResult:
        import torch

        if not text or not text.strip():
            return AnalysisResult(
                sentiment="neutral", sentiment_score=0.0, sentiment_confidence=0.0,
                topic="其他", reasoning="空文本",
            )

        inputs = self.tokenizer(
            text[:512], return_tensors="pt", truncation=True, max_length=512
        ).to(self.device)

        with torch.no_grad():
            logits = self.model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)[0].cpu().tolist()

        # 该模型 label 0=差评 1=好评（大众点评微调）
        neg_prob, pos_prob = probs[0], probs[1]
        if pos_prob > neg_prob:
            sentiment = "positive"
            score = pos_prob - neg_prob
        elif neg_prob > pos_prob:
            sentiment = "negative"
            score = -(neg_prob - pos_prob)
        else:
            sentiment = "neutral"
            score = 0.0

        return AnalysisResult(
            sentiment=sentiment,
            sentiment_score=float(score),
            sentiment_confidence=float(max(probs)),
            topic=None,  # 本地模型无主题分类能力
            reasoning=f"local model: neg={neg_prob:.2f} pos={pos_prob:.2f}",
            raw={"neg_prob": neg_prob, "pos_prob": pos_prob},
        )