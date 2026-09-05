"""Steam 采集器回归测试

锁住的回归（2026-09-03 对抗排查：底特律 9/3 02:00 运行 fetched=0 事故）：
1. Steam 瞬时空响应（限流/异常）≠ 翻页到底 —— 空页须退避重试，首页即空时
   last_cursor=None 会让验证页兜底失效，直接 break = 静默丢一整天数据
2. 应用层时间窗过滤：posted_before 之后的评论被排除；空响应重试保持同一 cursor
3. 连续 3 次空响应才放弃（防死循环）

全部用 fake session，不出网。
最后更新：2026-09-03
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    """按队列返回预设响应；记录每次请求参数"""

    def __init__(self, payloads):
        self._queue = list(payloads)
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return FakeResponse(self._queue.pop(0))


def _rev(rid: str, ts: int, content: str = "评价内容") -> dict:
    """构造最小合法的 Steam review dict（steamid 为合成值，非真实身份）"""
    return {
        "recommendationid": rid,
        "review": content,
        "timestamp_created": ts,
        "timestamp_updated": ts,
        "voted_up": True,
        "votes_up": 3,
        "language": "schinese",
        "author": {"steamid": "76561190000000000", "playtime_forever": 600},
    }


def _ts(*args) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


# 窗口与 2026-09-03 02:00 运行一致：[8/31 16:00, 9/2 16:00) UTC
AFTER = datetime(2026, 8, 31, 16, 0, 0)
BEFORE = datetime(2026, 9, 2, 16, 0, 0)


@pytest.fixture
def no_sleep(monkeypatch):
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda s: None)


def _collector():
    from src.collectors.steam import SteamCollector

    return SteamCollector(api_key="fake-key-for-test")


def test_empty_page_retried_then_recovers(no_sleep):
    """首页空响应 → 重试同 cursor 后拿到数据（修复前：直接 break，fetched=0）"""
    c = _collector()
    newer = _rev("r-new", _ts(2026, 9, 2, 20, 0), "窗口之后的评论")   # 应被排除
    in1 = _rev("r-in1", _ts(2026, 9, 1, 12, 0), "窗口内评论一号")
    in2 = _rev("r-in2", _ts(2026, 9, 1, 18, 44), "画面挺好的，剧情不能细想")
    old = _rev("r-old", _ts(2026, 8, 30, 12, 0), "窗口之前的评论")   # 应被排除

    c.session = FakeSession([
        {"success": 2},                                     # 瞬时空响应 → 重试
        {"reviews": [newer, in1, in2], "cursor": "c1",
         "query_summary": {"num_reviews": 3}},
        {"reviews": [old], "cursor": "c2",
         "query_summary": {"num_reviews": 1}},              # 整页在窗外 → streak 1
        {"success": 2}, {},                                 # 空响应重试 ×2
        {"success": 2},                                     # 第 3 次空 → 终止
    ])

    raws = list(c.collect(
        target_id="1222140", max_count=None, language="schinese",
        posted_after=AFTER, posted_before=BEFORE,
    ))

    assert len(raws) == 2, f"应恰好采到 2 条窗口内评论，实际 {len(raws)}"
    contents = [r.content for r in raws]
    assert "窗口内评论一号" in contents
    assert "画面挺好的，剧情不能细想" in contents
    # 时间窗过滤：窗口之后的评论不得混入
    assert all(r.posted_at < BEFORE for r in raws)
    assert all(r.posted_at >= AFTER for r in raws)
    # 重试保持了同一 cursor（"*"）
    assert c.session.calls[0]["cursor"] == "*"
    assert c.session.calls[1]["cursor"] == "*"


def _old_page(n: int) -> dict:
    """整页都是窗口之前的评论（ts < AFTER）→ page_new=0，累加 empty_streak"""
    return {
        "reviews": [_rev(f"r-old-{n}-{i}", _ts(2026, 8, 25, 12, 0), f"老评论 {n}-{i}")
                    for i in range(3)],
        "cursor": f"c-{n}",
        "query_summary": {"num_reviews": 3},
    }


def test_empty_streak_limit_terminates(no_sleep):
    """连续 8 页"有评论但全在窗外"（AUTO_EMPTY_STREAK_LIMIT）才终止（防死循环）。
    2026-09-03：recent 排序混序导致窗口内评论散落深页，3 阈值漏采 5-14%（黑神话
    214 实测 vs 203 采集），阈值提到 8。注意：本路径是 streak（窗外页累加），
    与空响应重试（reviews=[]）是两条独立终止路径。"""
    c = _collector()
    # 8 页窗外触发 streak 终止；第 9 个响应供 break 后的验证页消费
    # （验证页请求从 last_cursor 重取，返回仍是窗外页 → 无漏采救援，正常结束）
    c.session = FakeSession([_old_page(i) for i in range(8)] + [_old_page(99)])

    raws = list(c.collect(
        target_id="1222140", max_count=None, language="schinese",
        posted_after=AFTER, posted_before=BEFORE,
    ))

    assert raws == []          # 全在窗外，0 yield
    assert len(c.session.calls) == 9  # 8 页 streak 终止 + 1 验证页


def test_seven_streak_pages_do_not_stop(no_sleep):
    """7 页窗外（未达阈值 8）不终止；随后出现的窗口内评论被正常采集"""
    c = _collector()
    in1 = _rev("r-in", _ts(2026, 9, 1, 12, 0), "混序散落后恢复的评论")
    c.session = FakeSession(
        [_old_page(i) for i in range(7)]                        # streak 7（未达阈值 8）
        + [{"reviews": [in1], "cursor": "c-in",
            "query_summary": {"num_reviews": 1}}]               # 混序恢复：窗口内评论
        + [_old_page(i) for i in range(8)]                      # 再度 8 页窗外 → 终止
    )

    raws = list(c.collect(
        target_id="1222140", max_count=None, language="schinese",
        posted_after=AFTER, posted_before=BEFORE,
    ))

    assert len(raws) == 1 and raws[0].content == "混序散落后恢复的评论"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
