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
        filter: str = "recent",  # 默认改为 "recent"，因为 day_range 仅在 recent 时生效
        review_type: str = "all",  # 兼容旧参数（已弃用，使用 filter）
        fetch_metadata: bool = False,
        posted_after: datetime | None = None,
        posted_before: datetime | None = None,
        **kwargs,
    ) -> Iterator[RawComment]:
        """拉取指定游戏的评测

        Args:
            target_id: Steam appid（游戏的数字ID）
            max_count: 最大拉取数量
            language: 语言过滤（如 'schinese'、'english'、'all'）。
                **项目顶层原则**：Steam 平台只采集中文评论，所以默认 'schinese'。
                如需临时拉英文等其他语言，请显式传 language='english' 等。
            filter: Steam 排序方式
              - ``"recent"``：按创建时间倒序（**首次采集默认**，配合 day_range 限制时间窗口）
              - ``"updated"``：按更新时间倒序
              - ``"all"``：按 helpfulness 排序（**day_range 会被忽略**，避免时间窗失效）
            ⚠️ 重要：如果不传 posted_after / posted_before 且使用 filter="all"，
            会拉取游戏全量评论（按 helpfulness 排序），可能非常巨大。
            review_type: 兼容旧字段，已弃用，合并到 filter
            fetch_metadata: 是否回采点赞数与回复数。
                业务规则：**首次入库时必须传 False**（点赞数=0 无意义），
                评论发布满 7 天后由 scripts/refresh_likes.py 一次性回采时传 True。
            posted_after: 起始时间过滤（仅采集该时间之后的评论，Steam API 用 day_range 实现）。
                - day_range 范围 0-365（天），0 = 全部时间。
                - 当 posted_after 设了未来时间（如明天），会被忽略。
                - 当 posted_after 设了过去时间，最近 day_range 天。
            posted_before: 截止时间过滤（应用层，Steam API 不直接支持）。
                - 拉一批后过滤 timestamp_created < posted_before 的不 yield。

        Yields:
            RawComment 对象
        """
        # 兼容旧调用：如果只传 review_type 不传 filter，行为等同于旧版
        if filter == "all" and review_type != "all":
            filter = review_type

        url = STEAM_REVIEWS_URL.format(app_id=target_id)
        cursor = "*"
        fetched = 0

        # 计算 day_range（向下兼容 Steam API）：取 posted_after 距今天数
        # day_range 范围 1-365；None 表示不限制（传 0）
        # 重要发现：Steam API 的 day_range 参数**只对 filter="all" 生效**！
        # filter="recent" 下 day_range 被忽略，会返回游戏全量评论（按时间排序）。
        # 所以时间过滤必须用 posted_after / posted_before（应用层）实现。
        #
        # Steam 的"helpful 评论 day_range 4 天"语义如下：
        # - filter="all" + day_range=N → 仅返回最近 N 天内 Steam 标记的"helpful"评论
        # - filter="recent" + day_range=N → **不生效**，返回全部评论按时间排序
        # - filter="all" + day_range=0 → 全量评论按 helpfulness 排序
        day_range: int = 0  # 保留参数（始终传 0），避免 Steam 默认值语义不明

        # Steam API 翻页已知 bug：跨页时偶尔返回已见过的 recommendationid。
        # 为避免下游入库 UNIQUE 冲突，本地用 seen 集合去重。
        seen_source_ids: set[str] = set()
        empty_streak = 0  # 连续"无新数据"页数（>=3 视为已到时间窗外，停）

        while fetched < max_count:
            params = {
                "json": 1,
                "filter": filter,
                "language": language or "all",
                "cursor": cursor,
                "num_per_page": min(100, max_count - fetched),
                "purchase_type": "all",  # all / steam / non_steam_purchase
                "day_range": day_range,
            }

            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            reviews = data.get("reviews", [])
            if not reviews:
                # 出现空页就停止（Steam 翻页协议：服务端 cursor 一旦无效，
                # 返回的就该是空，没必要再多试一次）
                break

            page_new = 0
            for r in reviews:
                ts = r.get("timestamp_created")
                # posted_after 应用层过滤
                if posted_after is not None:
                    if ts is None or datetime.fromtimestamp(ts) < posted_after:
                        continue
                # posted_before 应用层过滤
                if posted_before is not None:
                    if ts is not None and datetime.fromtimestamp(ts) >= posted_before:
                        continue
                src_id = str(r.get("recommendationid", ""))
                if not src_id or src_id in seen_source_ids:
                    continue  # 跨页重复，跳过
                seen_source_ids.add(src_id)
                yield self._to_raw(target_id, r, fetch_metadata=fetch_metadata)
                fetched += 1
                page_new += 1
                if fetched >= max_count:
                    break

            # 翻页停止策略：
            # - filter="recent" 按时间倒序，page_new=0 说明这页都在时间窗外
            #   连续 3 页无新数据则可确认时间窗已越界，提前停止（避免翻几千页老评论）
            # - filter="all" 由 max_count 自然耗尽
            if page_new == 0:
                empty_streak += 1
                if filter == "recent" and empty_streak >= 3:
                    break
            else:
                empty_streak = 0

            cursor = data.get("cursor")
            # 当没有更多分页或游标为空时停止
            if not cursor or data.get("query_summary", {}).get("num_reviews", 0) == 0:
                break

    def _to_raw(
        self, appid: str, review: dict, *, fetch_metadata: bool = False
    ) -> RawComment:
        """将 Steam API 返回的评测转换为统一格式

        Args:
            appid: Steam appid
            review: Steam API 返回的单条评论 dict
            fetch_metadata: True 时记录 votes_up / comment_count / developer_response；
                False 时将 likes/replies 设为 None（首次入库用）

        Returns:
            RawComment 对象
        """
        # Steam 时间戳是 Unix 秒
        ts = review.get("timestamp_created")
        posted_at = datetime.fromtimestamp(ts) if ts else None

        # 推荐状态：1=推荐(好评) 0=不推荐(差评)
        voted_up = review.get("voted_up")
        rating = 1 if voted_up else 0 if voted_up is False else None

        # Steam 评测中的换行符清理
        content = review.get("review", "").strip()

        # 点赞数与回复数：仅回采（fetch_metadata=True）时才记录。
        # 首次采集置 None 表示"尚未回采"。
        likes: int | None = review.get("votes_up", 0) or 0 if fetch_metadata else None
        replies: int | None = review.get("comment_count", 0) or 0 if fetch_metadata else None

        author = review.get("author", {})
        # developer_response / timestamp_dev_responded：仅回采时记录。
        # 首次采集时不写入这两个 key（与 likes/replies 同机制：避免 0/空值误导），
        # 7 天后由回采脚本拉取真实值。
        extra: dict = {
            "appid": appid,
            "playtime_forever": author.get("playtime_forever", 0),
            "playtime_at_review": author.get("playtime_at_review", 0),
            "playtime_last_two_weeks": author.get("playtime_last_two_weeks", 0),
            "deck_playtime_at_review": author.get("deck_playtime_at_review", 0),
            "steam_purchase": review.get("steam_purchase"),
            "received_for_free": review.get("received_for_free"),
            "refunded": review.get("refunded"),
            "written_during_early_access": review.get("written_during_early_access"),
            "primarily_steam_deck": review.get("primarily_steam_deck"),
            "weighted_vote_score": review.get("weighted_vote_score"),
            "app_release_date": review.get("app_release_date"),
            "reactions": review.get("reactions", []),
        }
        if fetch_metadata:
            extra["developer_response"] = review.get("developer_response")
            extra["timestamp_dev_responded"] = review.get("timestamp_dev_responded")
        return RawComment(
            platform=self.platform,
            source_id=str(review.get("recommendationid", "")),
            content=content,
            author=author.get("steamid") or None,  # 仅保留匿名ID
            author_id=str(author.get("steamid", "")) or None,
            rating=rating,
            language=review.get("language"),
            likes=likes,
            replies=replies,
            posted_at=posted_at,
            extra=extra,
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