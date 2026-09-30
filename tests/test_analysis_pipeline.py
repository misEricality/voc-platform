"""端到端测试：run_pipeline 分析链路 → opinions 落库（P0.5 · 2026-08-14）

锁住的回归（此前主链路腐坏的根因，详见 2026-08-14 架构评审）：
1. pipeline 分析阶段不再引用已删除的 sub_topics 字段（AttributeError）
2. 观点（opinions）随主流程写入 comment_opinions 表
3. topic 由核心观点映射，正确落库
"""

import threading
import time
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
        # 单条接口：对齐真实 LLMSentimentAnalyzer.analyze（pipeline.py 逐条调用）
        return self.analyze_batch([text])[0]

    def analyze_batch(self, texts: list[str], **kwargs) -> list[AnalysisResult]:
        # 模拟方案4 输出：核心观点映射 topic，观点带完整路径（2026-09-08 批量接口）
        results = []
        for text in texts:
            if "打击感" in text:
                results.append(AnalysisResult(
                    sentiment="positive", sentiment_score=0.8, sentiment_confidence=0.9,
                    topic="玩法与内容",
                    opinions=[
                        Opinion(phrase="打击感超爽", sentiment="positive",
                                sentiment_score=0.8, sentiment_confidence=0.9,
                                is_core=True, l3="打击感",
                                full_path="玩法与内容/玩法机制/打击感"),
                    ],
                ))
            elif "价格" in text:
                results.append(AnalysisResult(
                    sentiment="negative", sentiment_score=-0.6, sentiment_confidence=0.85,
                    topic="商业与发行",
                    opinions=[
                        Opinion(phrase="价格太贵", sentiment="negative",
                                sentiment_score=-0.6, sentiment_confidence=0.85,
                                is_core=True, l3="定价",
                                full_path="商业与发行/价格与价值/定价"),
                    ],
                ))
            else:
                results.append(AnalysisResult(
                    sentiment="positive", sentiment_score=0.5, sentiment_confidence=0.7,
                    topic="叙事与表现",
                    opinions=[
                        Opinion(phrase="剧情很好", sentiment="positive",
                                sentiment_score=0.5, sentiment_confidence=0.7,
                                is_core=True, l3="主线",
                                full_path="叙事与表现/剧情叙事/主线"),
                    ],
                ))
        return results


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


# ==================== P1#1 / P3#2 对抗审查修复（2026-09-21） ====================

def _run_with_analyzer(tmp_path, monkeypatch, analyzer):
    """跑一次 run_pipeline（3 条评论），采集器/向量化已 mock，仅注入给定分析器"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'analysis.db'}")
    monkeypatch.setenv("LOG_LEVEL", "CRITICAL")

    from src.pipeline import run_pipeline, COLLECTORS

    monkeypatch.setitem(COLLECTORS, "steam", _FakeCollector)
    monkeypatch.setattr("src.pipeline.get_analyzer", lambda provider=None: analyzer)
    monkeypatch.setattr("src.pipeline.get_embedder", lambda: None)
    return run_pipeline("steam", "999999", max_count=3)


def test_pipeline_raises_when_analysis_batch_fails(tmp_path, monkeypatch):
    """P1#1：批级失败必须向上抛（raise_on_error=True）→ 目标判失败交哨兵重采。

    回归的是最重的数据完整性缺陷：原先逐条 analyze() 默认吞异常，DeepSeek
    超时/限流/400 会返回 neutral/conf=0 占位并被 update_analysis 固化 analyzed_at，
    当晚全部新评论永久标成 neutral 且永不重试、无告警。
    """
    class FailingAnalyzer:
        name = "failing"

        def analyze_batch(self, texts, **kwargs):
            assert kwargs.get("raise_on_error") is True, "主链路必须显式 raise_on_error=True"
            raise RuntimeError("DeepSeek 400 bad request")

    with pytest.raises(RuntimeError):
        _run_with_analyzer(tmp_path, monkeypatch, FailingAnalyzer())

    # 评论已入库（采集阶段），但分析失败 → analyzed_at 保持 NULL（下轮自动重试）
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        rows = list(s.execute(select(Comment)).scalars())
    assert len(rows) == 3
    assert all(c.analyzed_at is None for c in rows), "失败不得固化 analyzed_at"
    assert all(c.sentiment is None for c in rows)


def test_pipeline_skips_placeholder_results(tmp_path, monkeypatch):
    """P1#1 双保险：零置信度且无观点的「失败占位」不得落库（LLM 缺失 index 场景）"""
    class PlaceholderAnalyzer:
        name = "placeholder"

        def analyze_batch(self, texts, **kwargs):
            return [
                AnalysisResult(
                    sentiment="neutral", sentiment_score=0.0,
                    sentiment_confidence=0.0, opinions=[],
                )
                for _ in texts
            ]

    report = _run_with_analyzer(tmp_path, monkeypatch, PlaceholderAnalyzer())
    assert report["analyzed"] == 0
    assert report["analysis_skipped"] == 3

    _, SessionLocal = init_db()
    with SessionLocal() as s:
        rows = list(s.execute(select(Comment)).scalars())
    assert all(c.analyzed_at is None for c in rows), "占位结果不得固化，留待下轮重试"


def test_pipeline_filters_out_of_range_topic(tmp_path, monkeypatch):
    """P3#2：主链路把词表边界传给 update_analysis，越界 topic 落库前被置空"""
    class OutOfRangeAnalyzer:
        name = "oor"
        topic_primary = ["机制与内容"]  # 仅一个合法 L1

        def analyze_batch(self, texts, **kwargs):
            return [
                AnalysisResult(
                    sentiment="positive", sentiment_score=0.5,
                    sentiment_confidence=0.9, topic="不在词表里的标签", opinions=[],
                )
                for _ in texts
            ]

    report = _run_with_analyzer(tmp_path, monkeypatch, OutOfRangeAnalyzer())
    assert report["analyzed"] == 3

    _, SessionLocal = init_db()
    with SessionLocal() as s:
        rows = list(s.execute(select(Comment)).scalars())
    assert all(c.topic is None for c in rows), "越界 topic 应被 valid_l1_labels 过滤为 None"


# ==================== 标注并发（2026-10-01） ====================
#
# 背景：GLM-5.3-Flash 单请求延迟方差大（实测 p50 ~21s、见过 170.9s 尖峰），串行跑会让
# 02:00 链路更可能压进 03:00 哨兵窗口（哨兵在「02:00 仍在运行」时整个跳过 → 失败夜不补采）。
# 实测同一份 30 条样本：并发 1 → 64.5s，并发 3 → 23.5s（2.74×）。以下锁住并发不改变语义。

def _many_collector(n: int):
    """返回 1 个能产出 n 条评论的采集器类（内容各不相同，便于核对是否串批）"""

    class _ManyCollector:
        def fetch_app_info(self, target_id):
            return {"name": "Test Game", "type": "game"}

        def collect(self, target_id, max_count=50, language="schinese",
                    posted_after=None, posted_before=None):
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            return [
                RawComment(
                    platform="steam", source_id=f"m{i}", content=f"评论{i}",
                    author_id=f"u{i}", rating=1, language="schinese",
                    posted_at=now, extra={"appid": "999999"},
                )
                for i in range(n)
            ]

    return _ManyCollector


class _TrackingAnalyzer:
    """记录「同时进行中的批次数」峰值，并按文本回显观点（用于核对串批）"""

    name = "tracking"

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.inflight = 0
        self.max_inflight = 0
        self.calls = 0
        self._lock = threading.Lock()

    def analyze_batch(self, texts: list[str], **kwargs) -> list[AnalysisResult]:
        with self._lock:
            self.inflight += 1
            self.calls += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            time.sleep(self.delay)  # 制造重叠窗口：没有并发时 max_inflight 恒为 1
            return [
                AnalysisResult(
                    sentiment="positive", sentiment_score=0.5, sentiment_confidence=0.7,
                    topic="玩法与内容",
                    opinions=[Opinion(
                        phrase=t, sentiment="positive", sentiment_score=0.5,
                        sentiment_confidence=0.7, is_core=True, l3="动作系统",
                        full_path="玩法与内容/玩法机制/动作系统",
                    )],
                )
                for t in texts
            ]
        finally:
            with self._lock:
                self.inflight -= 1


def _run_many(tmp_path, monkeypatch, analyzer, n: int, concurrency: str | None):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'conc.db'}")
    monkeypatch.setenv("LOG_LEVEL", "CRITICAL")
    if concurrency is None:
        monkeypatch.delenv("ANALYZER_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("ANALYZER_CONCURRENCY", concurrency)

    from src.pipeline import run_pipeline, COLLECTORS

    monkeypatch.setitem(COLLECTORS, "steam", _many_collector(n))
    monkeypatch.setattr("src.pipeline.get_analyzer", lambda provider=None: analyzer)
    monkeypatch.setattr("src.pipeline.get_embedder", lambda: None)
    return run_pipeline("steam", "999999", max_count=n)


@pytest.mark.parametrize("raw,expected", [
    (None, 3),        # 未配置 → 默认 3
    ("", 3),          # 空串 → 默认（别把「空 env」当 0）
    ("1", 1),
    (" 4 ", 4),       # 容忍空白
    ("0", 3),         # 越界 → 回退默认并告警
    ("-2", 3),
    ("abc", 3),       # 手滑写错 → 回退默认，不能把跑批打挂
    ("99", 8),        # 超上限 → 按上限截断
])
def test_analysis_concurrency_env_parsing(monkeypatch, raw, expected):
    from src.pipeline import _analysis_concurrency

    if raw is None:
        monkeypatch.delenv("ANALYZER_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("ANALYZER_CONCURRENCY", raw)
    assert _analysis_concurrency() == expected


def test_analysis_runs_chunks_concurrently(tmp_path, monkeypatch):
    """25 条 → 3 批，默认并发 3：批次必须真的重叠（串行时 max_inflight 恒为 1）"""
    analyzer = _TrackingAnalyzer()
    report = _run_many(tmp_path, monkeypatch, analyzer, n=25, concurrency=None)

    assert report["analyzed"] == 25
    assert analyzer.max_inflight > 1, "默认应并发（串行时 max_inflight == 1）"
    assert analyzer.calls == 3, "25 条 / 10 条一批 = 3 批"


def test_analysis_concurrency_one_is_serial(tmp_path, monkeypatch):
    """ANALYZER_CONCURRENCY=1 → 回到串行（保底开关）"""
    analyzer = _TrackingAnalyzer()
    report = _run_many(tmp_path, monkeypatch, analyzer, n=25, concurrency="1")

    assert report["analyzed"] == 25
    assert analyzer.max_inflight == 1


def test_analysis_concurrency_preserves_per_comment_results(tmp_path, monkeypatch):
    """并发不得串批：每条评论落库的观点必须是**它自己**的（观点 phrase 回显文本）"""
    analyzer = _TrackingAnalyzer()
    report = _run_many(tmp_path, monkeypatch, analyzer, n=25, concurrency="3")
    assert report["analyzed"] == 25

    engine, SessionLocal = init_db()
    with SessionLocal() as s:
        rows = list(s.execute(select(Comment).order_by(Comment.source_id)).scalars())
        assert len(rows) == 25
        by_content = {c.content: c for c in rows}
        assert set(by_content) == {f"评论{i}" for i in range(25)}
        for c in rows:
            ops = list(s.execute(
                select(CommentOpinion).where(CommentOpinion.comment_id == c.id)
            ).scalars())
            assert [o.quote for o in ops] == [c.content], (
                f"{c.content} 的观点被串批：{ops}"
            )
        assert all(c.topic == "玩法与内容" for c in rows)

