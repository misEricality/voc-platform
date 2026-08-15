"""端到端测试：run_pipeline 分析链路 → opinions 落库（P0.5 · 2026-08-14）

锁住的回归（此前主链路腐坏的根因，详见 2026-08-14 架构评审）：
1. pipeline 分析阶段不再引用已删除的 sub_topics 字段（AttributeError）
2. 观点（opinions）随主流程写入 comment_opinions 表
3. topic 由核心观点映射，正确落库
"""

from datetime import datetime, timezone

import pytest

from src.collectors.base import RawComment
from src.analyzers.base import AnalysisResult, Opinion
from src.storage.db import init_db, CommentRepository, Comment, CommentOpinion
from sqlalchemy import select


def _fake_comments() -> list[RawComment]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return [
        RawComment(
            platform="steam",
            source_id=f"e{i}",
            content=t,
            author_id=f"u{i}",
            rating=1,
            language="schinese",
            posted_at=now,
            extra={"appid": "999999"},
        )
        for i, t in enumerate(["打击感超爽但优化太差", "价格太贵", "剧情很好"])
    ]


class _FakeCollector:
    """替代 SteamCollector：fetch_app_info + collect 返回固定评论"""

    def fetch_app_info(self, target_id):
        return {"name": "Test Game", "type": "game"}

    def collect(self, target_id, max_count=50, language="schinese",
                posted_after=None, posted_before=None):
        return _fake_comments()


class _FakeAnalyzer:
    """替代 LLM 分析器：模拟 _finalize 之后的最终结果（topic 已映射、opinions 带 full_path）"""

    name = "fake"

    def analyze(self, text: str, *, context: dict | None = None) -> AnalysisResult:
        # 模拟方案4 输出：核心观点映射 topic，观点带完整路径
        if "打击感" in text:
            return AnalysisResult(
                sentiment="positive", sentiment_score=0.8, sentiment_confidence=0.9,
                topic="玩法与内容",
                opinions=[
                    Opinion(phrase="打击感超爽", sentiment="positive",
                            sentiment_score=0.8, sentiment_confidence=0.9,
                            is_core=True, l3="打击感",
                            full_path="玩法与内容/玩法机制/打击感"),
                ],
            )
        if "价格" in text:
            return AnalysisResult(
                sentiment="negative", sentiment_score=-0.6, sentiment_confidence=0.85,
                topic="商业与发行",
                opinions=[
                    Opinion(phrase="价格太贵", sentiment="negative",
                            sentiment_score=-0.6, sentiment_confidence=0.85,
                            is_core=True, l3="定价",
                            full_path="商业与发行/价格与价值/定价"),
                ],
            )
        return AnalysisResult(
            sentiment="positive", sentiment_score=0.5, sentiment_confidence=0.7,
            topic="叙事与表现",
            opinions=[
                Opinion(phrase="剧情很好", sentiment="positive",
                        sentiment_score=0.5, sentiment_confidence=0.7,
                        is_core=True, l3="主线",
                        full_path="叙事与表现/剧情叙事/主线"),
            ],
        )


def test_pipeline_analysis_writes_opinions(tmp_path, monkeypatch):
    """主链路端到端：run_pipeline 分析阶段应把观点写入 comment_opinions"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test_voc.db'}")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    from src.pipeline import run_pipeline, COLLECTORS

    # mock 采集器 + 分析器 + 向量化（跳过 embedding 模型加载，聚焦"分析→落盘"链路）
    monkeypatch.setitem(COLLECTORS, "steam", _FakeCollector)
    monkeypatch.setattr("src.pipeline.get_analyzer", lambda provider=None: _FakeAnalyzer())
    monkeypatch.setattr("src.pipeline.get_embedder", lambda: None)

    # 不 skip_analysis → 走完整分析链路
    report = run_pipeline("steam", "999999", max_count=3)

    assert report["fetched"] == 3
    assert report["analyzed"] == 3, "主流程应完成 3 条分析（此前 sub_topics AttributeError 会中断）"

    # 验证落库
    engine, SessionLocal = init_db()
    session = SessionLocal()

    comments = list(session.execute(select(Comment).order_by(Comment.source_id)).scalars())
    assert len(comments) == 3

    # topic 由核心观点映射，正确落库
    topics = {c.source_id: c.topic for c in comments}
    assert topics["e0"] == "玩法与内容"
    assert topics["e1"] == "商业与发行"
    assert topics["e2"] == "叙事与表现"

    # 观点写入 comment_opinions（每条 1 个观点）
    opinions = list(session.execute(select(CommentOpinion)).scalars())
    assert len(opinions) == 3, "观点应随主流程落库到 comment_opinions"
    full_paths = {o.full_path for o in opinions}
    assert "玩法与内容/玩法机制/打击感" in full_paths
    assert "商业与发行/价格与价值/定价" in full_paths
    assert "叙事与表现/剧情叙事/主线" in full_paths

    # 观点级 confidence 也落库
    assert all(o.sentiment_confidence is not None for o in opinions)

    session.close()
