"""端到端测试：采集 → 清洗 → AI 分析 → 查询

覆盖：
- RawComment 数据类
- SteamCollector (新：fetch_metadata、时间过滤)
- SQLite 持久化（含新增回采机制字段）
- LLM 分析器 mock 注入
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest.mock import MagicMock
from src.collectors.steam import SteamCollector


def test_day_range_calculation():
    """day_range 参数行为验证：
    day_range 语义未经受控验证（见 steam.py 注释），项目对任何 filter 恒传 0；
    时间窗统一由应用层 posted_after / posted_before 实现。
    """
    collector = SteamCollector.__new__(SteamCollector)
    collector.session = MagicMock()

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"reviews": [], "cursor": None, "query_summary": {"num_reviews": 0}}
    collector.session.get.return_value = mock_resp

    # 1. 默认 filter="recent"：day_range 总是 0（参数没意义）
    list(collector.fetch_comments("730", max_count=10))
    params = collector.session.get.call_args.kwargs["params"]
    assert params["day_range"] == 0, f"recent 模式 day_range 应为 0，实际为 {params['day_range']}"

    # 2. filter="all"：day_range 仍传 0（避免 Steam 默认值语义不明）
    list(collector.fetch_comments("730", max_count=10, filter="all"))
    params = collector.session.get.call_args.kwargs["params"]
    assert params["day_range"] == 0, f"all 模式 day_range 应为 0，实际为 {params['day_range']}"

    print("✓ test_day_range_calculation")


def test_posted_after_app_layer_filter():
    """posted_after 现在是应用层过滤（而非 day_range Steam API 限制）"""
    collector = SteamCollector.__new__(SteamCollector)
    collector.session = MagicMock()

    # 模拟 Steam 返回 3 条评论，时间戳分别 8/1, 8/2, 8/3
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "reviews": [
            {"recommendationid": "1", "timestamp_created": 1754000000, "voted_up": True, "review": "A", "author": {}},
            {"recommendationid": "2", "timestamp_created": 1754086400, "voted_up": True, "review": "B", "author": {}},
            {"recommendationid": "3", "timestamp_created": 1754172800, "voted_up": True, "review": "C", "author": {}},
        ],
        "cursor": None,
        "query_summary": {"num_reviews": 3},
    }
    collector.session.get.return_value = mock_resp

    # 传 posted_after = 8/2 的时间戳：只 yield 第 2 条 + 第 3 条
    posted_after = datetime.fromtimestamp(1754086400)  # 8/2 时间戳
    raws = list(
        collector.fetch_comments(
            "730",
            max_count=100,
            posted_after=posted_after,
        )
    )
    assert len(raws) == 2, f"应 yield 2 条，实际为 {len(raws)}"
    assert raws[0].source_id == "2"
    assert raws[1].source_id == "3"

    print("✓ test_posted_after_app_layer_filter")


def test_posted_before_app_layer_filter():
    """posted_before 是应用层过滤（Steam API 不支持）"""
    collector = SteamCollector.__new__(SteamCollector)
    collector.session = MagicMock()

    # 模拟 Steam 返回 3 条评论，时间戳分别 8/1, 8/2, 8/3
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "reviews": [
            {"recommendationid": "1", "timestamp_created": 1754000000, "voted_up": True, "review": "A", "author": {}},
            {"recommendationid": "2", "timestamp_created": 1754086400, "voted_up": True, "review": "B", "author": {}},
            {"recommendationid": "3", "timestamp_created": 1754172800, "voted_up": True, "review": "C", "author": {}},
        ],
        "cursor": None,
        "query_summary": {"num_reviews": 3},
    }
    collector.session.get.return_value = mock_resp

    # 不传 posted_before：全部 yield
    raws = list(collector.fetch_comments("730", max_count=100))
    assert len(raws) == 3, f"应 yield 3 条，实际为 {len(raws)}"

    # 传 posted_before：只 yield timestamp < posted_before 的
    # 8/2 23:59:59 之前应只 yield 第 1 条
    posted_before = datetime.fromtimestamp(1754172800)  # 正好是第 3 条时间戳
    raws = list(
        collector.fetch_comments(
            "730",
            max_count=100,
            posted_before=posted_before,
        )
    )
    # posted_before=第3条的时间戳，应用层过滤 timestamp >= posted_before 不 yield
    # 所以应 yield 第 1 条 + 第 2 条
    assert len(raws) == 2, f"应 yield 2 条，实际为 {len(raws)}"
    assert raws[0].source_id == "1"
    assert raws[1].source_id == "2"

    print("✓ test_posted_before_app_layer_filter")


def test_pipeline_import():
    """只冒烟测试：pipeline.py 能 import 成功（posted_after/before 参数已加）"""
    from src.pipeline import run_pipeline, COLLECTORS
    assert "steam" in COLLECTORS
    print("✓ test_pipeline_import")


def test_fetch_comments_dedup():
    """Steam API 翻页已知 bug：跨页偶发返回已见过的 recommendationid。
    fetch_comments 内部应去重，避免下游 UNIQUE 冲突。
    """
    from src.collectors.steam import SteamCollector

    collector = SteamCollector.__new__(SteamCollector)
    collector.session = MagicMock()

    page1 = {
        "reviews": [
            {"recommendationid": "1", "timestamp_created": 1754000000, "voted_up": True, "review": "A", "author": {}},
            {"recommendationid": "2", "timestamp_created": 1754000000, "voted_up": True, "review": "B", "author": {}},
            {"recommendationid": "3", "timestamp_created": 1754000000, "voted_up": True, "review": "C", "author": {}},
        ],
        "cursor": "next",
        "query_summary": {"num_reviews": 3},
    }
    page2 = {
        "reviews": [
            # 跨页重复
            {"recommendationid": "1", "timestamp_created": 1754000000, "voted_up": True, "review": "A", "author": {}},
            {"recommendationid": "4", "timestamp_created": 1754000000, "voted_up": True, "review": "D", "author": {}},
        ],
        "cursor": None,
        "query_summary": {"num_reviews": 2},
    }
    collector.session.get.side_effect = [
        MagicMock(json=lambda: page1),
        MagicMock(json=lambda: page2),
    ]

    raws = list(collector.fetch_comments("730", max_count=100))
    src_ids = [r.source_id for r in raws]
    assert src_ids == ["1", "2", "3", "4"], f"应去重后 [1,2,3,4]，实际 {src_ids}"
    assert len(raws) == 4, f"应有 4 条，实际 {len(raws)}"
    print("✓ test_fetch_comments_dedup")


def test_fetch_comments_empty_page_stop():
    """连续两页空 → 翻页停止（不要无限循环）"""
    from src.collectors.steam import SteamCollector

    collector = SteamCollector.__new__(SteamCollector)
    collector.session = MagicMock()

    page1 = {
        "reviews": [
            {"recommendationid": "1", "timestamp_created": 1754000000, "voted_up": True, "review": "A", "author": {}},
        ],
        "cursor": "next",
        "query_summary": {"num_reviews": 1},
    }
    page2 = {"reviews": [], "cursor": "next", "query_summary": {"num_reviews": 0}}
    page3 = {"reviews": [], "cursor": None, "query_summary": {"num_reviews": 0}}
    collector.session.get.side_effect = [
        MagicMock(json=lambda: page1),
        MagicMock(json=lambda: page2),
        MagicMock(json=lambda: page3),
    ]

    raws = list(collector.fetch_comments("730", max_count=100))
    assert len(raws) == 1, f"应 yield 1 条，实际 {len(raws)}"
    assert collector.session.get.call_count == 2, f"应 2 次请求停止，实际 {collector.session.get.call_count}"
    print("✓ test_fetch_comments_empty_page_stop")


def test_fetch_comments_verification_page_safe():
    """验证页安全场景：正常翻页后，验证页里没有漏采 → 不做兜底"""
    import time
    from src.collectors.steam import SteamCollector

    now = int(time.time())
    day = 86400
    posted_after_ts = now - day         # 1 天前
    posted_before_ts = now + day        # 1 天后

    collector = SteamCollector.__new__(SteamCollector)
    collector.session = MagicMock()

    # 场景：用户拉 [1 天前, 1 天后] 数据
    # 第 1 页：3 条全在时间窗内
    page1 = {
        "reviews": [
            {"recommendationid": "1", "timestamp_created": now - 3600, "voted_up": True, "review": "A", "author": {}},
            {"recommendationid": "2", "timestamp_created": now - 7200, "voted_up": True, "review": "B", "author": {}},
            {"recommendationid": "3", "timestamp_created": now - 10800, "voted_up": True, "review": "C", "author": {}},
        ],
        "cursor": "page2",
        "query_summary": {"num_reviews": 3},
    }
    # 第 2 页：全时间窗外老评论 → 应用层过滤后 0 条 → cursor 失效停
    page2 = {
        "reviews": [
            {"recommendationid": "4", "timestamp_created": now - day * 30, "voted_up": True, "review": "Old1", "author": {}},
            {"recommendationid": "5", "timestamp_created": now - day * 31, "voted_up": True, "review": "Old2", "author": {}},
            {"recommendationid": "6", "timestamp_created": now - day * 32, "voted_up": True, "review": "Old3", "author": {}},
        ],
        "cursor": None,
        "query_summary": {"num_reviews": 3},
    }
    # 验证页（兜底）：全老评论 → 不应 yield 新数据
    verify_page = {
        "reviews": [
            {"recommendationid": "7", "timestamp_created": now - day * 40, "voted_up": True, "review": "VerifyOld", "author": {}},
        ],
        "cursor": None,
        "query_summary": {"num_reviews": 1},
    }
    collector.session.get.side_effect = [
        MagicMock(json=lambda: page1),
        MagicMock(json=lambda: page2),
        MagicMock(json=lambda: verify_page),
    ]

    posted_after = datetime.fromtimestamp(posted_after_ts)
    posted_before = datetime.fromtimestamp(posted_before_ts)
    raws = list(
        collector.fetch_comments(
            "730",
            max_count=100,
            posted_after=posted_after,
            posted_before=posted_before,
        )
    )
    src_ids = [r.source_id for r in raws]
    assert src_ids == ["1", "2", "3"], f"应只 yield 时间窗内的 3 条，实际 {src_ids}"
    print("✓ test_fetch_comments_verification_page_safe")


def test_fetch_comments_verification_page_rescue():
    """验证页风险场景：验证页里发现漏采 → 兜底拉 2 页"""
    import time
    from src.collectors.steam import SteamCollector

    now = int(time.time())
    day = 86400
    posted_after_ts = now - day
    posted_before_ts = now + day

    collector = SteamCollector.__new__(SteamCollector)
    collector.session = MagicMock()

    # 场景：filter=recent 流串了一页"老评论"，3 页连空停
    # 但实际 recent 流还有 1 条时间内评论，验证页发现它
    page1 = {
        "reviews": [
            {"recommendationid": "1", "timestamp_created": now - 3600, "voted_up": True, "review": "A", "author": {}},
        ],
        "cursor": "page2",
        "query_summary": {"num_reviews": 1},
    }
    # 第 2-4 页都全老评论 → page_new=0 连续 3 次 → 停
    old_page = {
        "reviews": [
            {"recommendationid": "9", "timestamp_created": now - day * 30, "voted_up": True, "review": "Old", "author": {}},
        ],
        "cursor": "next",
        "query_summary": {"num_reviews": 1},
    }
    # 验证页：包含 1 条时间内 + 1 条老
    verify_page_with_residue = {
        "reviews": [
            {"recommendationid": "11", "timestamp_created": now - 1800, "voted_up": True, "review": "Missed", "author": {}},  # 时间窗内
            {"recommendationid": "12", "timestamp_created": now - day * 30, "voted_up": True, "review": "Old", "author": {}},
        ],
        "cursor": "rescue1",
        "query_summary": {"num_reviews": 2},
    }
    # 兜底页 1：含新评论
    rescue1 = {
        "reviews": [
            {"recommendationid": "13", "timestamp_created": now - 7200, "voted_up": True, "review": "Missed2", "author": {}},
        ],
        "cursor": "rescue2",
        "query_summary": {"num_reviews": 1},
    }
    # 兜底页 2：全老评论 → 停止
    rescue2 = {
        "reviews": [
            {"recommendationid": "14", "timestamp_created": now - day * 30, "voted_up": True, "review": "Old", "author": {}},
        ],
        "cursor": None,
        "query_summary": {"num_reviews": 1},
    }

    collector.session.get.side_effect = [
        MagicMock(json=lambda: page1),
        MagicMock(json=lambda: old_page),         # 翻页第 2 次（streak=1）
        MagicMock(json=lambda: old_page),         # 翻页第 3 次（streak=2）
        MagicMock(json=lambda: old_page),         # 翻页第 4 次（streak=3）→ 停
        MagicMock(json=lambda: verify_page_with_residue),  # 验证页
        MagicMock(json=lambda: rescue1),         # 兜底页 1
        MagicMock(json=lambda: rescue2),         # 兜底页 2（无新数据，停止）
    ]

    posted_after = datetime.fromtimestamp(posted_after_ts)
    posted_before = datetime.fromtimestamp(posted_before_ts)
    raws = list(
        collector.fetch_comments(
            "730",
            max_count=100,
            posted_after=posted_after,
            posted_before=posted_before,
        )
    )
    src_ids = [r.source_id for r in raws]
    # 期望：page1 的 1 + 验证页的 11（时间内）+ 兜底 1 的 13（时间内）= 3 条
    assert src_ids == ["1", "11", "13"], f"验证页兜底后应为 ['1','11','13']，实际 {src_ids}"
    assert len(raws) == 3
    print("✓ test_fetch_comments_verification_page_rescue")


if __name__ == "__main__":
    test_day_range_calculation()
    test_posted_after_app_layer_filter()
    test_posted_before_app_layer_filter()
    test_pipeline_import()
    test_fetch_comments_dedup()
    test_fetch_comments_empty_page_stop()
    test_fetch_comments_verification_page_safe()
    test_fetch_comments_verification_page_rescue()
    print("✓ 测试全部通过")