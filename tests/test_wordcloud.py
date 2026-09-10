"""评论词云（compare 页 · 2026-09-08）单元测试

锁住的回归：
1. jieba 分词 + 停用词/噪声过滤（config/wordlists/wordcloud_stopwords.txt）
2. 跨游戏 TF-IDF 区分度：两游戏共有的词权重低于独有词
3. 词的主导情感标签（词内情感多数决，一词一色）
4. 最低词频门槛与低数据放宽
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def wordcloud_db(tmp_path, monkeypatch):
    """独立测试 DB：两个 Steam 目标各若干评论（刻意构造区分词/共有词/停用词/情感分布）"""
    db_path = tmp_path / "wc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    from src.storage.db import Comment, init_db

    _, SessionLocal = init_db(f"sqlite:///{db_path}")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    n = 0
    with SessionLocal() as s:
        rows = []
        # 目标 A：独有词「悟空」高频 + 正向；共有词「优化」两游戏都有
        for i in range(10):
            rows.append(Comment(
                platform="steam", source_id=f"a{i}", target_id="steam:111",
                content=f"悟空的战斗系统真是精彩，优化也不错，剧情很棒{i}",
                posted_at=now - timedelta(days=i), sentiment="positive",
            ))
        # 目标 B：独有词「回合」高频 + 负向
        for i in range(10):
            rows.append(Comment(
                platform="steam", source_id=f"b{i}", target_id="steam:222",
                content=f"回合制玩法太无聊了，优化一般，回合设计重复{i}",
                posted_at=now - timedelta(days=i), sentiment="negative",
            ))
        s.add_all(rows)
        n = len(rows)
        s.commit()
    return {"session_factory": SessionLocal, "rows": n}


def test_stopwords_loaded_and_token_filter():
    """停用词表应生效：功能词/通用游戏词被过滤，纯数字/标点被丢弃"""
    from src.api import service

    assert "的" in service._cloud_stopwords()
    assert "优化" not in service._cloud_stopwords()  # 内容词不能误杀
    toks = service._cloud_tokens("这个游戏真的太棒了！！123 http://x.cn 优化优化")
    assert "这个" not in toks and "游戏" not in toks and "真的" not in toks
    assert "优化" in toks
    assert all(len(t) >= 2 for t in toks)


def test_tfidf_distinctiveness_and_sentiment(wordcloud_db):
    """跨游戏共有词（优化）的 weight 应低于各游戏独有词（悟空/回合）；词色=词内多数情感"""
    from src.api import service

    with wordcloud_db["session_factory"]() as s:
        payload = service.wordcloud_payload(s, ["steam:111", "steam:222"])

    by_t = {it["target_id"]: {w["word"]: w for w in it["words"]} for it in payload["items"]}
    a, b = by_t["steam:111"], by_t["steam:222"]
    assert "悟空" in a and "回合" in b and "优化" in a and "优化" in b
    # 独有词 df=1（idf 高），共有词 df=2（idf 低）；tf 相近 → 独有词权重大
    assert a["悟空"]["weight"] > a["优化"]["weight"]
    assert b["回合"]["weight"] > b["优化"]["weight"]
    # 情感标签：词内多数决
    assert a["悟空"]["sentiment"] == "positive"
    assert b["回合"]["sentiment"] == "negative"
    # count/share 字段在位
    assert a["悟空"]["count"] >= 5 and 0 < a["悟空"]["share"] <= 100


def test_wordcloud_cache_hit(wordcloud_db):
    """同参数重复调用走进程内缓存（返回同一对象）"""
    from src.api import service

    with wordcloud_db["session_factory"]() as s:
        p1 = service.wordcloud_payload(s, ["steam:111"])
        p2 = service.wordcloud_payload(s, ["steam:111"])
    assert p1 is p2


def test_wordcloud_window_and_rebuild(wordcloud_db):
    """窗口过滤按 posted_at 日生效；新增评论后（count/max_id 指纹变化）缓存失效重建"""
    from datetime import datetime, timedelta

    from src.api import service

    now = datetime.utcnow()
    start = (now - timedelta(days=15)).strftime("%Y-%m-%d")
    end = (now - timedelta(days=9)).strftime("%Y-%m-%d")  # 只覆盖最早 1 天（days=i, i=0..9 → i=9）
    with wordcloud_db["session_factory"]() as s:
        p_win = service.wordcloud_payload(s, ["steam:111"], start=start, end=end)
        a_win = next(it for it in p_win["items"] if it["target_id"] == "steam:111")
        full = service.wordcloud_payload(s, ["steam:111"])
        a_full = next(it for it in full["items"] if it["target_id"] == "steam:111")
        assert a_win["total_tokens"] < a_full["total_tokens"]

    # 新增评论 → 指纹变化 → 目标级矩阵重建
    from src.storage.db import Comment

    with wordcloud_db["session_factory"]() as s:
        s.add(Comment(
            platform="steam", source_id="new1", target_id="steam:111",
            content="全新词汇横空出世", posted_at=now, sentiment="positive",
        ))
        s.commit()
        p_after = service.wordcloud_payload(s, ["steam:111"])
        a_after = next(it for it in p_after["items"] if it["target_id"] == "steam:111")
        words = {w["word"] for w in a_after["words"]}
        assert "全新" in words or "词汇" in words or "横空" in words  # 新评论已计入
        assert a_after["total_tokens"] > a_full["total_tokens"]
