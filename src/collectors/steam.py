"""Steam 游戏评测采集器

基于 Steam 官方 Web API：
  https://partner.steamgames.com/doc/store/getreviews

特点：
- 官方 API，无需登录态，合规稳定
- 公开评测内容，研究学习可用
- 支持按游戏 appid 拉取，分页 cursors
- 支持语言过滤、评测类型过滤（推荐/全部）

申请 API Key：
  https://steamcommunity.com/dev/apikey
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Iterator

import requests

from .base import BaseCollector, RawComment

# Steam 评测 API 端点
STEAM_REVIEWS_URL = "https://store.steampowered.com/appreviews/{app_id}"
# Steam 应用详情 API（用于补充游戏元数据）
STEAM_APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"


class SteamCollector(BaseCollector):
    """Steam 游戏评测采集器"""

    platform = "steam"

    def __init__(self, api_key: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key or os.getenv("STEAM_API_KEY")
        if not self.api_key:
            print("[WARN] STEAM_API_KEY 未设置，部分接口可能受限")
        self.session = requests.Session()
        # Steam 接口要求 User-Agent
        self.session.headers.update(
            {
                "User-Agent": "VoC-Platform/0.1 (Learning Project)",
                "Accept": "application/json",
            }
        )

    def fetch_comments(
        self,
        target_id: str,
        *,
        max_count: int = 100,
        language: str | None = "schinese",
        review_type: str = "all",  # all / positive / negative
        **kwargs,
    ) -> Iterator[RawComment]:
        """拉取指定游戏的评测

        Args:
            target_id: Steam appid（游戏的数字ID）
            max_count: 最大拉取数量
            language: 语言过滤（如 'schinese'、'english'、'all'）
            review_type: 评测类型 all/positive/negative

        Yields:
            RawComment 对象
        """
        url = STEAM_REVIEWS_URL.format(app_id=target_id)
        cursor = "*"
        fetched = 0

        while fetched < max_count:
            params = {
                "json": 1,
                "filter": review_type,
                "language": language or "all",
                "cursor": cursor,
                "num_per_page": min(100, max_count - fetched),
                "purchase_type": "all",  # all / steam / non_steam_purchase
                "day_range": 0,  # 0 = 全部时间
            }

            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            reviews = data.get("reviews", [])
            if not reviews:
                break

            for r in reviews:
                yield self._to_raw(target_id, r)
                fetched += 1
                if fetched >= max_count:
                    break

            cursor = data.get("cursor")
            # 当没有更多分页或游标为空时停止
            if not cursor or data.get("query_summary", {}).get("num_reviews", 0) == 0:
                break

    def _to_raw(self, appid: str, review: dict) -> RawComment:
        """将 Steam API 返回的评测转换为统一格式"""
        # Steam 时间戳是 Unix 秒
        ts = review.get("timestamp_created")
        posted_at = datetime.fromtimestamp(ts) if ts else None

        # 推荐状态：1=推荐(好评) 0=不推荐(差评)
        voted_up = review.get("voted_up")
        rating = 1 if voted_up else 0 if voted_up is False else None

        # Steam 评测中的换行符清理
        content = review.get("review", "").strip()

        return RawComment(
            platform=self.platform,
            source_id=str(review.get("recommendationid", "")),
            content=content,
            author=review.get("author", {}).get("steamid") or None,  # 仅保留匿名ID
            author_id=str(review.get("author", {}).get("steamid", "")) or None,
            rating=rating,
            language=review.get("language"),
            likes=review.get("votes_up", 0) or 0,
            replies=review.get("comment_count", 0) or 0,
            posted_at=posted_at,
            extra={
                "appid": appid,
                "playtime_forever": review.get("author", {}).get("playtime_forever", 0),
                "playtime_at_review": review.get("author", {}).get("playtime_at_review", 0),
                "steam_purchase": review.get("steam_purchase"),
                "received_for_free": review.get("received_for_free"),
                "written_during_early_access": review.get("written_during_early_access"),
            },
        )

    def fetch_app_info(self, appid: str) -> dict | None:
        """获取游戏元数据（名称、类型、开发商等）

        用于在仪表盘中展示游戏标题
        """
        try:
            resp = self.session.get(
                STEAM_APP_DETAILS_URL,
                params={"appids": appid, "cc": "cn", "l": "schinese"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get(str(appid), {}).get("data") if data else None
        except Exception as e:
            print(f"[WARN] 获取 appid={appid} 元数据失败：{e}")
            return None


# 一些热门游戏 appid（用于快速测试）
POPULAR_GAMES = {
    "730": "CS2",
    "570": "Dota 2",
    "1172470": "Apex Legends",
    "1245620": "Elden Ring",
    "892970": "Valheim",
    "1623730": "Palworld",
    "1966720": "Lethal Company",
    "1091500": "Cyberpunk 2077",
    "1817070": "Marvel Rivals",
}


if __name__ == "__main__":
    # 快速冒烟测试
    collector = SteamCollector()
    appid = "730"  # CS2
    print(f"测试采集 appid={appid} ({POPULAR_GAMES.get(appid, 'Unknown')}) ...")
    comments = collector.collect(appid, max_count=5, language="schinese")
    for c in comments:
        print(f"  [{c.rating}] {c.content[:80]}...")
    print(f"采集完成，共 {len(comments)} 条")