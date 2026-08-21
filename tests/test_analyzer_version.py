"""analyzer_version 字段溯源测试（2026-08-21 P10）

锁住的回归：
1. Comment.analyzer_version 字段存在且可写
2. update_analysis 接受 analyzer_version 入参并写入
3. analyze_llm 的 analyzer_version 属性 = "{name}:{model}@{prompt_hash8}"
4. compute_prompt_set_hash() 稳定：固定输入 → 固定输出；任一 prompt 改动 → hash 变
5. 不传 analyzer_version 时不擦旧值（向后兼容旧调用）

测试用独立测试 DB（参照 test_daily_incremental_collect.py 的 fixture 模式），
绝不碰 data/voc.db；写 DB 后清理。
"""
from __future__ import annotations

import importlib
import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ---------- fixtures ----------

@pytest.fixture
def test_db_path():
    """每个用例分配独立测试 DB（data/voc_test_<uuid>.db，已 .gitignore 排除 *.db）。"""
    db = ROOT / "data" / f"voc_test_{uuid.uuid4().hex[:8]}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    yield db
    if db.exists():
        try:
            db.unlink()
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _isolated_env(test_db_path, monkeypatch):
    """隔离 DATABASE_URL + 抑制真实 embedder/analyzer 加载。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setattr("src.pipeline.get_embedder", lambda: None)
    yield


# ---------- 用例 1：Comment 模型有 analyzer_version 字段 ----------

def test_comment_has_analyzer_version_column():
    """Comment 模型必须包含 analyzer_version 列（String(64), nullable=True, indexed）"""
    from src.storage.db import Comment

    col = Comment.__table__.columns.get("analyzer_version")
    assert col is not None, "Comment.analyzer_version 列必须存在"
    # String 类型、nullable、可索引
    assert col.type.length == 64
    assert col.nullable is True
    # 索引：column 应在某个 Index 里（直接断言 index=True 等价于列有 index）
    assert col.index is True, "analyzer_version 应建索引（按版本分组查询用）"


# ---------- 用例 2：update_analysis 接受并写入 analyzer_version ----------

def test_update_analysis_writes_analyzer_version(test_db_path):
    """repo.update_analysis(..., analyzer_version=...) 写入到 Comment 行"""
    from src.collectors.base import RawComment
    from src.storage.db import init_db, CommentRepository

    engine, SessionLocal = init_db()
    with SessionLocal() as s:
        repo = CommentRepository(s)
        rc = RawComment(
            platform="steam", source_id="r-av1", content="测试评论", rating=1,
            language="schinese",
            extra={"appid": "999"},
        )
        c = repo.upsert(rc, target_meta={"name": "Test"})
        repo.commit()

        repo.update_analysis(
            c.id,
            sentiment="positive",
            sentiment_score=0.8,
            sentiment_confidence=0.9,
            topic="玩法与内容",
            analyzer_version="llm:deepseek-v4-flash@a3f9e7c2",
        )
        repo.commit()

        from sqlalchemy import select
        from src.storage.db import Comment as _Comment
        row = s.execute(select(_Comment)).scalars().one()
        assert row.analyzer_version == "llm:deepseek-v4-flash@a3f9e7c2", \
            f"应写入 analyzer_version，实际为 {row.analyzer_version!r}"


# ---------- 用例 3：不传 analyzer_version 时不擦旧值 ----------

def test_update_analysis_preserves_existing_analyzer_version_when_none(test_db_path):
    """向后兼容：不传 analyzer_version 不应擦掉已有值"""
    from src.collectors.base import RawComment
    from src.storage.db import init_db, CommentRepository, Comment

    engine, SessionLocal = init_db()
    with SessionLocal() as s:
        repo = CommentRepository(s)
        c = repo.upsert(RawComment(
            platform="steam", source_id="r-av2", content="测试评论 2", rating=0,
            language="schinese", extra={"appid": "999"},
        ), target_meta={"name": "Test"})
        repo.commit()

        # 第一次写入 v1
        repo.update_analysis(
            c.id, sentiment="negative", sentiment_score=-0.5,
            sentiment_confidence=0.7, topic="玩法与内容",
            analyzer_version="llm:deepseek-v4-flash@aaaa1111",
        )
        repo.commit()

        # 第二次不传 analyzer_version（模拟旧 caller）
        repo.update_analysis(
            c.id, sentiment="positive", sentiment_score=0.3,
            sentiment_confidence=0.8, topic="玩法与内容",
        )
        repo.commit()

        from sqlalchemy import select
        row = s.execute(select(Comment)).scalars().one()
        assert row.analyzer_version == "llm:deepseek-v4-flash@aaaa1111", \
            f"已有 analyzer_version 应保留，实际为 {row.analyzer_version!r}"
        # 其他字段正常更新
        assert row.sentiment == "positive"


# ---------- 用例 4：compute_prompt_set_hash 稳定 ----------

def test_prompt_set_hash_is_stable():
    """同一 prompt 集合 → 同一 hash"""
    from src.analyzers.sentiment_llm import compute_prompt_set_hash

    h1 = compute_prompt_set_hash()
    h2 = compute_prompt_set_hash()
    assert h1 == h2, "同一输入应得到同一 hash"
    # 8 位 hex（不严格，但 8 位约定）
    assert len(h1) == 8
    assert all(c in "0123456789abcdef" for c in h1), f"hash 应为 hex，实际 {h1!r}"


# ---------- 用例 5：prompt 内容变化 → hash 变 ----------

def test_prompt_change_changes_hash(monkeypatch):
    """任一 prompt 文件内容改动 → 集合 hash 变"""
    from src.analyzers import sentiment_llm
    from src.analyzers.sentiment_llm import compute_prompt_set_hash

    original = compute_prompt_set_hash()

    # Monkey-patch _load_prompt：等价于 prompt 内容改了一行
    original_loader = sentiment_llm._load_prompt

    def patched(name):
        text = original_loader(name)
        return text + "\n# monkeypatch touched"

    monkeypatch.setattr(sentiment_llm, "_load_prompt", patched)
    # compute_prompt_set_hash 直接读文件，不走 _load_prompt，所以 monkeypatch 这条不行
    # → 改为 monkeypatch hashlib 模拟（更准确），或 monkeypatch 文件系统读。
    # 这里用更直接的：写一份临时 fake prompt 文件替换原文件。
    # 但写在 .gitignore 目录（data/），且要恢复。

    fake_path = sentiment_llm.PROMPTS_DIR / "sentiment.txt"
    bak = fake_path.read_bytes()
    try:
        fake_path.write_bytes(bak + b"\n# monkeypatch touched\n")
        new_hash = compute_prompt_set_hash()
        assert new_hash != original, f"prompt 改后 hash 应变；原 {original} 新 {new_hash}"
    finally:
        fake_path.write_bytes(bak)
    # 恢复后 hash 应回到原值
    assert compute_prompt_set_hash() == original


# ---------- 用例 6：analyzer_version 格式正确 ----------

def test_llm_analyzer_version_format(monkeypatch):
    """LLM 分析器的 analyzer_version = '{name}:{model}@{prompt_hash8}'"""
    # 不需要真实 API Key（只校验属性计算，不调 LLM）
    fake_key = "sk-fake-for-version-check-only"
    monkeypatch.setenv("DEEPSEEK_API_KEY", fake_key)

    from src.analyzers.sentiment_llm import LLMSentimentAnalyzer

    a = LLMSentimentAnalyzer(provider="deepseek")
    v = a.analyzer_version
    # 拆三段
    assert v.startswith("llm:"), f"应 'llm:' 开头，实际 {v!r}"
    parts = v.split("@")
    assert len(parts) == 2, f"应有且仅有一个 @，实际 {v!r}"
    head, hash8 = parts
    # head = "llm:{model}"
    assert head.startswith("llm:")
    model = head[len("llm:"):]
    assert model == a.model, f"head 中 model 应等于实例 model={a.model}，实际 {model!r}"
    # hash8 = prompt 集合 hash
    assert hash8 == a.prompt_hash
    assert len(hash8) == 8


# ---------- 用例 7：local analyzer 也有 analyzer_version ----------

def test_local_analyzer_version_format():
    """LocalSentimentAnalyzer.analyzer_version = '{name}:{model}@local'（无 prompt）"""
    from src.analyzers.sentiment_local import LocalSentimentAnalyzer

    # 不实际加载模型（只校验属性逻辑）：
    # 直接调 @property 而不构造（构造会下载模型）
    # 临时构造一个空实例（绕过 __init__）
    a = LocalSentimentAnalyzer.__new__(LocalSentimentAnalyzer)
    a.model_name = "uer/roberta-base-finetuned-dianping-chinese"
    v = a.analyzer_version
    assert v == "local:uer/roberta-base-finetuned-dianping-chinese@local", \
        f"格式应为 'local:uer/...@local'，实际 {v!r}"


# ---------- 用例 8：pipeline 把 analyzer_version 传给 update_analysis ----------

def test_pipeline_passes_analyzer_version(monkeypatch, test_db_path):
    """pipeline.run_pipeline 调用 update_analysis 时必须传 analyzer_version 入参"""
    from src.collectors.base import RawComment
    from src.pipeline import run_pipeline, COLLECTORS

    captured: dict = {}

    # Fake analyzer：analyzer_version 必须存在
    class FakeAnalyzer:
        name = "fake"

        @property
        def analyzer_version(self):
            return "fake:test@aabbccdd"

        def analyze(self, text, *, context=None):
            from src.analyzers.base import AnalysisResult
            return AnalysisResult(
                sentiment="positive", sentiment_score=0.5,
                sentiment_confidence=0.9, topic="玩法与内容", opinions=[],
            )

    # Wrap update_analysis 抓取入参
    from src.storage import db as db_mod
    original_update = db_mod.CommentRepository.update_analysis

    def spy_update(self, comment_id, **kwargs):
        captured.setdefault("versions", []).append(kwargs.get("analyzer_version"))
        return original_update(self, comment_id, **kwargs)

    monkeypatch.setattr(db_mod.CommentRepository, "update_analysis", spy_update)
    monkeypatch.setattr("src.pipeline.get_analyzer", lambda provider=None: FakeAnalyzer())

    # Fake collector：注入 1 条评论
    class FakeCollector:
        def fetch_app_info(self, target_id):
            return {"name": "Test", "type": "game"}

        def collect(self, target_id, max_count=50, language="schinese",
                    posted_after=None, posted_before=None):
            from datetime import datetime, timezone
            return [RawComment(
                platform="steam", source_id="r-pipe1", content="测试",
                rating=1, language="schinese",
                posted_at=datetime.now(timezone.utc).replace(tzinfo=None),
                extra={"appid": target_id},
            )]

    monkeypatch.setitem(COLLECTORS, "steam", FakeCollector)

    report = run_pipeline("steam", "999", max_count=1, skip_analysis=False)
    assert report["analyzed"] == 1
    assert captured.get("versions") == ["fake:test@aabbccdd"], \
        f"应传 'fake:test@aabbccdd'，实际 {captured.get('versions')!r}"


# ---------- 用例 9：analyzer 不暴露 analyzer_version → None（旧 caller 兼容） ----------

def test_pipeline_handles_missing_analyzer_version_gracefully(monkeypatch):
    """analyzer 没有 analyzer_version 属性时（极旧代码/自定义 fake）→ 传 None，不崩"""
    from src.collectors.base import RawComment
    from src.pipeline import run_pipeline, COLLECTORS

    captured: list = []

    class FakeAnalyzerNoVersion:
        name = "no-version"

        # 故意没有 analyzer_version 属性
        def analyze(self, text, *, context=None):
            from src.analyzers.base import AnalysisResult
            return AnalysisResult(
                sentiment="positive", sentiment_score=0.5,
                sentiment_confidence=0.9, topic="玩法与内容", opinions=[],
            )

    from src.storage import db as db_mod
    original_update = db_mod.CommentRepository.update_analysis

    def spy_update(self, comment_id, **kwargs):
        captured.append(kwargs.get("analyzer_version"))
        return original_update(self, comment_id, **kwargs)

    monkeypatch.setattr(db_mod.CommentRepository, "update_analysis", spy_update)
    monkeypatch.setattr("src.pipeline.get_analyzer", lambda provider=None: FakeAnalyzerNoVersion())

    class FakeCollector:
        def fetch_app_info(self, target_id):
            return {"name": "Test", "type": "game"}

        def collect(self, target_id, max_count=50, language="schinese",
                    posted_after=None, posted_before=None):
            from datetime import datetime, timezone
            return [RawComment(
                platform="steam", source_id="r-pipe2", content="测试",
                rating=1, language="schinese",
                posted_at=datetime.now(timezone.utc).replace(tzinfo=None),
                extra={"appid": target_id},
            )]

    monkeypatch.setitem(COLLECTORS, "steam", FakeCollector)

    report = run_pipeline("steam", "999", max_count=1, skip_analysis=False)
    assert report["analyzed"] == 1
    assert captured == [None], f"无 analyzer_version 属性时传 None，实际 {captured!r}"


# ---------- 用例 10：init_db 自动 ALTER 老库（轻量 schema 演进） ----------

def test_init_db_auto_alters_missing_nullable_column(test_db_path):
    """老 DB 缺 analyzer_version 列时，init_db 应自动 ADD COLUMN

    模拟真实场景：老库结构基本对齐（所有 NOT NULL 列都在），但缺新加的 analyzer_version 列。
    """
    from sqlalchemy import create_engine, text
    # 1. 先用纯 SQLite 建一张"老版本"的 comments 表（含所有 NOT NULL 列，但缺 analyzer_version）
    raw_engine = create_engine(f"sqlite:///{test_db_path}")
    with raw_engine.begin() as conn:
        from src.storage.db import Comment
        cols_no_av = [c for c in Comment.__table__.columns if c.name != "analyzer_version"]
        col_defs = []
        for c in cols_no_av:
            t = c.type.compile(raw_engine.dialect)
            nullable = "" if c.nullable else " NOT NULL"
            col_defs.append(f"{c.name} {t}{nullable}")
        pk = list(Comment.__table__.primary_key.columns.values())[0]
        pk_def = f"{pk.name} INTEGER PRIMARY KEY AUTOINCREMENT"
        col_defs = [d for d in col_defs if not d.startswith(pk.name + " ")]
        conn.execute(text(
            f"CREATE TABLE comments ({pk_def}, " + ", ".join(col_defs) + ")"
        ))
    raw_engine.dispose()

    # 2. init_db() 应自动加 analyzer_version 列
    from src.storage.db import init_db
    engine, _ = init_db()
    with engine.begin() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(comments)")).fetchall()]
        assert "analyzer_version" in cols, \
            f"analyzer_version 应被自动 ADD COLUMN，实际列：{cols}"
    # 幂等：再 init_db 一次不应报错（缺列已不存在 → 不走 ALTER）
    init_db()

    # 3. ALTER 后写入应生效（生产模式：bulk_upsert 自动 commit，再 update_analysis）
    from src.storage.db import CommentRepository
    from src.collectors.base import RawComment
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        repo = CommentRepository(s)
        rc = RawComment(platform="steam", source_id="r-auto", content="x", extra={"appid": "999"})
        # bulk_upsert 内部 commit → 写入主键 → 重新查得 obj
        repo.bulk_upsert([rc], target_meta={"name": "X"})
        from sqlalchemy import select
        from src.storage.db import Comment as _Comment
        c = s.execute(select(_Comment)).scalars().one()
        assert c.id is not None, "bulk_upsert 后应有 id"
        repo.update_analysis(c.id, sentiment="positive", sentiment_score=0.5,
                             sentiment_confidence=0.5, analyzer_version="llm:any@12345678")
        repo.commit()
        row = s.execute(select(_Comment)).scalars().one()
        assert row.analyzer_version == "llm:any@12345678"


# ---------- main（直接跑时） ----------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])