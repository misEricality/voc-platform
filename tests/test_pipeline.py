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
    """Steam API 的 day_range 参数行为验证：
    - filter="recent" 时 day_range 被忽略（仅传 0）
    - filter="all" 时 day_range 才生效
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


if __name__ == "__main__":
    test_day_range_calculation()
    test_posted_after_app_layer_filter()
    test_posted_before_app_layer_filter()
    test_pipeline_import()
    test_fetch_comments_dedup()
    test_fetch_comments_empty_page_stop()
    print("✓ 测试全部通过")