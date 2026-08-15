"""向量化集成测试（2026-08-11）

覆盖：
- pipeline 入库后自动向量化新评论（skip_analysis 时也执行）
- 向量落库 + 单空间断言（表内只有一种模型）
- 语义检索可返回结果
- 依赖：本地已缓存 bge-small-zh-v1.5（无网络也可跑）；模型缺失时自动 skip
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from src.collectors.base import RawComment
from src.storage.db import init_db, CommentRepository


def _embedder_available() -> bool:
    from src.analyzers.embedder import get_embedder

    return get_embedder() is not None


def _fake_comments(target_id: str) -> list[RawComment]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    texts = [
        "服务器天天掉线，延迟极高，加速器都救不了",
        "打击感很棒，手感一流，连招流畅",
        "画面不错但优化太差，9700K 都掉帧",
    ]
    return [
        RawComment(
            platform="steam",
            source_id=f"e{i}",
            content=t,
            author_id=f"u{i}",
            rating=1,
            language="schinese",
            posted_at=now,
            extra={"appid": target_id},
        )
        for i, t in enumerate(texts)
    ]


def test_pipeline_embeds_new_comments(tmp_path, monkeypatch):
    """pipeline 入库后自动向量化（skip_analysis=True 时仍执行，与打标解耦）"""
    if not _embedder_available():
        pytest.skip("sentence-transformers 或模型不可用，跳过向量化集成测试")

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test_voc.db'}")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    from src.pipeline import run_pipeline, COLLECTORS
    from src.collectors.steam import SteamCollector

    class FakeCollector:
        def __init__(self):
            pass

        def fetch_app_info(self, target_id):
            return {"name": "Test Game", "type": "game"}

        def collect(self, target_id, max_count=50, language="schinese", posted_after=None, posted_before=None):
            return _fake_comments(target_id)

    monkeypatch.setitem(COLLECTORS, "steam", FakeCollector)
    report = run_pipeline("steam", "999999", max_count=3, skip_analysis=True)

    assert report["fetched"] == 3
    assert report["embedded"] == 3, "新入库评论应被向量化"

    engine, SessionLocal = init_db()
    repo = CommentRepository(SessionLocal())

    # 落库 + 单空间断言
    assert repo.count_embeddings() == 3
    models = repo.embedding_models_in_use()
    assert len(models) == 1, "表内只允许一种模型（单空间）"

    # 语义检索能召回（"服务器/掉线" 应命中第一条）
    from src.analyzers.embedder import semantic_search

    hits = semantic_search(repo, "服务器 掉线 延迟", top_k=1)
    assert hits, "语义检索应返回结果"
    assert "服务器" in hits[0]["content"], f"检索结果应相关，实际: {hits[0]['content'][:40]}"

    # 幂等：重复跑 pipeline 不产生重复向量
    report2 = run_pipeline("steam", "999999", max_count=3, skip_analysis=True)
    assert report2["embedded"] == 0, "已有向量的评论不应重复编码"
    assert repo.count_embeddings() == 3

    print("✓ test_pipeline_embeds_new_comments")
