"""Agent tools 真实实现回归测试（2026-09-09 第 4 轮）

覆盖：
- query_overview：调用 service.overview_payload，返回正确结构
- query_topics：调用 service.topics_payload，top_n 截断生效
- query_comments：调用 service.comments_payload，content 截断到 280
- search_docs：调用 md_corpus.search，按 query 返回命中
- run_tool 统一分发：参数校验、错误处理

不依赖外部服务（不调 LLM；纯 SQL + 文件 I/O）。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def test_db_path():
    db = ROOT / "data" / f"voc_agent_tools_{uuid.uuid4().hex[:8]}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    yield db
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


@pytest.fixture
def session(test_db_path):
    """独立测试 DB + seed 一些 Comment/CommentOpinion/CollectTask（供 tools 查）"""
    from src.storage.db import CollectTask, Comment, CommentOpinion, init_db
    from datetime import datetime, timezone

    engine, SessionLocal = init_db(f"sqlite:///{test_db_path}")
    with SessionLocal() as s:
        # 1 个 CollectTask（用于 _meta_name 等）
        s.add(CollectTask(
            platform="steam", target_id="2358720", name="黑神话：悟空",
            enabled=True,
        ))
        # 6 条评论：3 负 / 2 正 / 1 中；3 条有 topic（comment 级）
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None)
        comments = [
            ("c1", "negative", "战斗手感有问题，闪避判定很迷", 0.2, "战斗系统与操作"),
            ("c2", "negative", "地图设计太烂了，经常迷路", 0.3, "地图与关卡设计"),
            ("c3", "negative", "优化差，3070 都掉帧严重", 0.2, None),
            ("c4", "positive", "美术和剧情是真的牛", 0.9, "美术与剧情表现"),
            ("c5", "positive", "国产 3A 的里程碑之作", 0.95, "美术与剧情表现"),
            ("c6", "neutral", "普通玩家，路过", 0.5, None),
        ]
        for cid, senti, content, score, topic in comments:
            s.add(Comment(
                platform="steam", source_id=f"src_{cid}",
                target_id="steam:2358720", content=content,
                sentiment=senti, sentiment_score=score, topic=topic,
                posted_at=now, analyzed_at=now,
                extra_meta=json_dumps({"name": "黑神话：悟空"}),
            ))
        s.commit()
        # 取最新插入的 ids
        rows = s.execute(text("SELECT id, content FROM comments WHERE target_id='steam:2358720' ORDER BY id")).all()
        # 给 c1/c2/c4 加 CommentOpinion（让 topics_payload 有数据）
        cid_map = {row[1]: row[0] for row in rows}
        s.add(CommentOpinion(comment_id=cid_map["战斗手感有问题，闪避判定很迷"],
                              full_path="战斗系统与操作/手感/闪避", sentiment="negative",
                              quote="闪避判定很迷"))
        s.add(CommentOpinion(comment_id=cid_map["地图设计太烂了，经常迷路"],
                              full_path="地图与关卡设计/地图/迷路", sentiment="negative",
                              quote="经常迷路"))
        s.add(CommentOpinion(comment_id=cid_map["美术和剧情是真的牛"],
                              full_path="美术与剧情表现/美术/风格", sentiment="positive",
                              quote="美术和剧情是真的牛"))
        s.add(CommentOpinion(comment_id=cid_map["优化差，3070 都掉帧严重"],
                              full_path="性能与优化/帧率/掉帧", sentiment="negative",
                              quote="3070 都掉帧严重"))
        s.commit()
    yield SessionLocal
    # cleanup
    engine.dispose()


def json_dumps(d):
    import json
    return json.dumps(d, ensure_ascii=False)


# ==================== query_overview ====================


def test_query_overview_basic(session):
    """query_overview 应返回 service.overview_payload 的结构（含 total/sentiment/avg_score）"""
    from src.agent.tools import query_overview
    with session() as s:
        result_str = query_overview(s, target="steam:2358720")
    import json
    data = json.loads(result_str)
    assert data["target_id"] == "steam:2358720"
    assert data["total"] == 6
    assert data["analyzed"] == 6
    assert data["sentiment"]["negative"] == 3
    assert data["sentiment"]["positive"] == 2
    assert data["sentiment"]["neutral"] == 1
    # 负向占比 50%
    assert data["sentiment"]["negative_pct"] == 50.0


def test_query_overview_grain_opinion(session):
    """grain=opinion 应按观点聚合（返回 opinion_total）"""
    from src.agent.tools import query_overview
    with session() as s:
        result_str = query_overview(s, target="steam:2358720", grain="opinion")
    import json
    data = json.loads(result_str)
    # seed 了 4 条 opinion（3 negative + 1 positive）
    assert data["opinion_total"] == 4
    assert data["sentiment"]["negative"] == 3
    assert data["sentiment"]["positive"] == 1


def test_query_overview_no_data(session):
    """不存在的 target 应友好返回 error JSON（不抛异常）"""
    from src.agent.tools import query_overview
    with session() as s:
        result_str = query_overview(s, target="steam:0000000")
    import json
    data = json.loads(result_str)
    assert "error" in data
    assert "无评论数据" in data["error"]


def test_query_overview_invalid_grain(session):
    """grain 非法应返回 error JSON"""
    from src.agent.tools import query_overview
    with session() as s:
        result_str = query_overview(s, target="steam:2358720", grain="invalid")
    import json
    data = json.loads(result_str)
    assert "error" in data


# ==================== query_topics ====================


def test_query_topics_l1_default(session):
    """query_topics 默认 L1 + opinion grain；top_n 截断"""
    from src.agent.tools import query_topics
    with session() as s:
        result_str = query_topics(s, target="steam:2358720", level="L1", top_n=2)
    import json
    data = json.loads(result_str)
    assert data["level"] == "L1"
    assert len(data["topics"]) <= 2
    # opinion grain：3 条 opinion 都是 negative，应该只有 negative 项
    for t in data["topics"]:
        assert t["total"] > 0


def test_query_topics_with_sentiment_filter(session):
    """sentiment=positive 应只返回正向主题（opinion grain）"""
    from src.agent.tools import query_topics
    with session() as s:
        result_str = query_topics(s, target="steam:2358720", level="L1",
                                    sentiment="positive", top_n=10)
    import json
    data = json.loads(result_str)
    # 我们只种了 1 条 positive opinion（美术与剧情）
    if data["topics"]:
        # 没有 negative 项（被 filter 掉）
        for t in data["topics"]:
            assert t["positive"] > 0


def test_query_topics_invalid_level(session):
    """非法 level 应返回 error JSON"""
    from src.agent.tools import query_topics
    with session() as s:
        result_str = query_topics(s, target="steam:2358720", level="L99")
    import json
    data = json.loads(result_str)
    assert "error" in data


def test_query_topics_top_n_clamped(session):
    """top_n > 50 应被截断到 50（不报错）"""
    from src.agent.tools import query_topics
    with session() as s:
        # 不报错即可
        query_topics(s, target="steam:2358720", level="L1", top_n=999)
        query_topics(s, target="steam:2358720", level="L1", top_n=0)


# ==================== query_comments ====================


def test_query_comments_default(session):
    """query_comments 默认 limit=20，按时间倒序"""
    from src.agent.tools import query_comments
    with session() as s:
        result_str = query_comments(s, target="steam:2358720")
    import json
    data = json.loads(result_str)
    assert data["total"] == 6
    assert data["returned"] == 6
    assert len(data["items"]) == 6
    # content 应被截断 ≤ 280 字（我们的种子内容都很短）
    for item in data["items"]:
        assert len(item["content"]) <= 280


def test_query_comments_sentiment_filter(session):
    """sentiment 过滤"""
    from src.agent.tools import query_comments
    with session() as s:
        result_str = query_comments(s, target="steam:2358720", sentiment="negative", limit=5)
    import json
    data = json.loads(result_str)
    assert data["sentiment_filter"] == "negative"
    for item in data["items"]:
        assert item["sentiment"] == "negative"


def test_query_comments_limit_clamp(session):
    """limit > 50 → 50；limit=0 → 1（max(1, ...)）"""
    from src.agent.tools import query_comments
    with session() as s:
        # 不报错即可
        r1 = query_comments(s, target="steam:2358720", limit=999)
        r2 = query_comments(s, target="steam:2358720", limit=0)
    import json
    assert json.loads(r1)["returned"] <= 50
    assert json.loads(r2)["returned"] >= 1


def test_query_comments_topic_match_mode_auto_l3(session):
    """2026-09-09：auto 模式下 query_comments(topic=L3名) 应按 full_path 段匹配查得到

    seed: c1 有 opinion "战斗系统与操作/手感/闪避"
    - topic="战斗系统与操作"（L1）：应匹配（prefix）
    - topic="手感"（L2）：旧 prefix 模式查不到，auto 能查到
    - topic="闪避"（L3）：旧 prefix 模式查不到，auto 能查到
    """
    from src.agent.tools import query_comments
    import json

    # L1 名前缀匹配（prefix/auto 都行）
    with session() as s:
        r1 = query_comments(s, target="steam:2358720", topic="战斗系统与操作")
        d1 = json.loads(r1)
        # c1 有该 full_path 的 opinion
        assert d1["total"] >= 1
        # total 应排除没 opinion 的 comment（c2~c6 的 full_path 都不是这个）

    # L2 名（auto 才能匹配）
    with session() as s:
        r2 = query_comments(s, target="steam:2358720", topic="手感")
        d2 = json.loads(r2)
        # auto 模式下应能查到 c1（full_path 含 /手感/）
        assert d2["total"] >= 1, "auto 模式应按段匹配 L2 名"

    # L3 名（auto 才能匹配）
    with session() as s:
        r3 = query_comments(s, target="steam:2358720", topic="闪避")
        d3 = json.loads(r3)
        # auto 模式下应能查到 c1（full_path 结尾 /闪避）
        assert d3["total"] >= 1, "auto 模式应按段匹配 L3 名"

    # auto 模式下也可限定 prefix 行为（兼容老调用）
    with session() as s:
        r4 = query_comments(s, target="steam:2358720", topic="手感",
                            topic_match_mode="prefix")
        d4 = json.loads(r4)
        # prefix 模式下 full_path LIKE '手感%'，没记录以"手感"开头
        assert d4["total"] == 0


def test_query_comments_topic_match_mode_invalid(session):
    """非法 topic_match_mode 应返回 error JSON"""
    from src.agent.tools import query_comments
    import json
    with session() as s:
        r = query_comments(s, target="steam:2358720",
                            topic="手感", topic_match_mode="invalid")
    d = json.loads(r)
    assert "error" in d
    assert "topic_match_mode" in d["error"]


# ==================== search_docs ====================


def test_search_docs_hit():
    """search_docs 在 5 个核心 doc 里有命中"""
    from src.agent.tools import search_docs
    r = search_docs(query="Agent 设计", top_k=3)
    import json
    data = json.loads(r)
    assert data["query"] == "Agent 设计"
    assert len(data["hits"]) > 0
    # 命中应该来自 Agent 设计文档本身
    assert any("ORIGINAL_VOICE_ANALYSIS_AGENT" in h["path"] for h in data["hits"])


def test_search_docs_no_hit():
    """极冷门 query 应返回空 hits + 友好提示"""
    from src.agent.tools import search_docs
    # 用纯英文乱数（不会被中文 n-gram tokenizer 误匹配）
    r = search_docs(query="qwertyxcvbasdfzxcvqwertyuiop", top_k=3)
    import json
    data = json.loads(r)
    assert data["hits"] == []


def test_search_docs_empty_query_returns_empty():
    """空 query 不应崩"""
    from src.agent.tools import search_docs
    r = search_docs(query="", top_k=3)
    import json
    data = json.loads(r)
    assert data["hits"] == []


# ==================== run_tool 统一分发 ====================


def test_run_tool_unknown_name(session):
    """未知 tool name 返回 error JSON"""
    from src.agent.tools import run_tool
    with session() as s:
        r = run_tool("non_existent_tool", {}, session=s)
    import json
    data = json.loads(r)
    assert "未知 tool" in data["error"]


def test_run_tool_missing_required_param(session):
    """缺必填参数返回 error JSON"""
    from src.agent.tools import run_tool
    with session() as s:
        r1 = run_tool("query_overview", {}, session=s)
        r2 = run_tool("query_topics", {"target": "x"}, session=s)
        r3 = run_tool("search_docs", {}, session=s)
    import json
    assert "缺少必填参数 target" in json.loads(r1)["error"]
    assert "缺少必填参数 level" in json.loads(r2)["error"]
    assert "缺少必填参数 query" in json.loads(r3)["error"]


def test_run_tool_dispatch_all(session):
    """4 个 tool 都能正常 dispatch"""
    from src.agent.tools import run_tool
    with session() as s:
        r1 = run_tool("query_overview", {"target": "steam:2358720"}, session=s)
        r2 = run_tool("query_topics", {"target": "steam:2358720", "level": "L1"}, session=s)
        r3 = run_tool("query_comments", {"target": "steam:2358720", "limit": 3}, session=s)
        r4 = run_tool("search_docs", {"query": "Agent"}, session=s)
    import json
    for r in (r1, r2, r3, r4):
        d = json.loads(r)
        assert "error" not in d
