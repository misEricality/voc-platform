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

import logging
import os
from datetime import datetime, timezone
from typing import Iterator

import requests

from .base import BaseCollector, RawComment

log = logging.getLogger("voc.collectors.steam")

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
        max_count: int | None = None,
        language: str | None = "schinese",
        filter: str = "recent",  # 时间倒序（默认）；时间窗走应用层过滤（见下方 day_range 说明）
        review_type: str = "all",  # 兼容旧参数（已弃用，使用 filter）
        fetch_metadata: bool = False,
        posted_after: datetime | None = None,
        posted_before: datetime | None = None,
        **kwargs,
    ) -> Iterator[RawComment]:
        """拉取指定游戏的评测

        Args:
            target_id: Steam appid（游戏的数字ID）
            max_count: 单次采集上限。``None`` = 自动模式：不设硬上限，
                靠时间窗（posted_after/posted_before）+ 自然翻页终止逻辑
                （连续 3 页无新数据 + 验证页）在窗口边界自动停止，
                从而自适应各游戏/各阶段的真实量。
                ⚠️ 自动模式依赖时间窗：若 max_count=None 且未传任何时间窗，
                会拉取游戏全量评测（可能非常巨大），故自动模式强制要求时间窗。
            language: 语言过滤（如 'schinese'、'english'、'all'）。
                **项目顶层原则**：Steam 平台只采集中文评论，所以默认 'schinese'。
                如需临时拉英文等其他语言，请显式传 language='english' 等。
            filter: Steam 排序方式
              - ``"recent"``：按创建时间倒序（**首次采集默认**）
              - ``"updated"``：按更新时间倒序
              - ``"all"``：按 helpfulness 排序
            ⚠️ 重要：如果不传 posted_after / posted_before 且使用 filter="all"，
            会拉取游戏全量评论（按 helpfulness 排序），可能非常巨大。
            review_type: 兼容旧字段，已弃用，合并到 filter
            fetch_metadata: 是否回采点赞数与回复数。
                业务规则：**首次入库时必须传 False**（点赞数=0 无意义），
                评论发布满 7 天后由 scripts/refresh_likes.py 一次性回采时传 True。
            posted_after: 起始时间过滤（仅采集该时间之后的评论，应用层实现；
                day_range 语义未受控验证，项目恒传 0，不依赖 Steam 自身时间窗）。
            posted_before: 截止时间过滤（应用层，Steam API 不直接支持）。
                - 拉一批后过滤 timestamp_created < posted_before 的不 yield。

        Yields:
            RawComment 对象
        """
        # 自动模式（max_count=None）必须配时间窗，否则会拉全量
        if max_count is None and posted_after is None and posted_before is None:
            raise ValueError(
                "max_count=None（自动模式）必须配合 posted_after/posted_before 时间窗，"
                "否则会拉取游戏全量评论"
            )
        auto = max_count is None

        # 兼容旧调用：如果只传 review_type 不传 filter，行为等同于旧版
        if filter == "all" and review_type != "all":
            filter = review_type

        url = STEAM_REVIEWS_URL.format(app_id=target_id)
        cursor = "*"
        fetched = 0

        # ⚠️ day_range 语义说明（2026-08-15 架构评审整理）：
        # 项目历史上对 day_range 的生效条件存在两种相互矛盾的记载
        # （"仅 recent 生效" vs "仅 all 生效"），两者均未经受控实验验证。
        # 因此不依赖 Steam 自身时间窗：恒传 0，时间过滤统一用
        # posted_after / posted_before 在应用层实现（见 _passes_time_filter）。
        day_range: int = 0  # 恒传 0（语义未验证，不依赖）

        # Steam API 翻页已知 bug：跨页时偶尔返回已见过的 recommendationid。
        # 为避免下游入库 UNIQUE 冲突，本地用 seen 集合去重。
        seen_source_ids: set[str] = set()
        empty_streak = 0  # 连续"无新数据"页数（>=3 视为已到时间窗外，停）
        last_cursor: str | None = None  # 翻页停止后用于"验证页"的游标

        def _passes_time_filter(ts: int | None) -> bool:
            """应用层时间过滤（posted_after / posted_before）

            时间统一按 UTC 处理（naive），与 posted_at 落库口径一致。
            """
            if posted_after is not None:
                if ts is None or datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) < posted_after:
                    return False
            if posted_before is not None:
                if ts is not None and datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) >= posted_before:
                    return False
            return True

        while auto or fetched < max_count:
            params = {
                "json": 1,
                "filter": filter,
                "language": language or "all",
                "cursor": cursor,
                "num_per_page": min(100, max_count - fetched) if not auto else 100,
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
                # 应用层时间过滤
                if not _passes_time_filter(ts):
                    continue
                src_id = str(r.get("recommendationid", ""))
                if not src_id or src_id in seen_source_ids:
                    continue  # 跨页重复，跳过
                seen_source_ids.add(src_id)
                yield self._to_raw(target_id, r, fetch_metadata=fetch_metadata)
                fetched += 1
                page_new += 1
                if not auto and fetched >= max_count:
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
            last_cursor = cursor

        # =====================================================================
        # 验证页（Verification Page）
        # =====================================================================
        # 翻页"自然停止"后，再额外拉 1 页确认。
        # 为什么需要：filter="recent" 排序不是 100% 严格时间倒序，可能混入少量老评论。
        # 连续 3 页空停、cursor 失效停、max_count 触顶停 → 都可能漏采最新的几条。
        # 验证页策略：再拉 1 页，如还有"时间内"的评论 → 警告 + 继续翻。
        #
        # 触发条件：仅在用户指定了 posted_after 或 posted_before 时启用（时间窗有意义时）。
        # 时间成本：单次采集 +1 页（~0.5-1 秒）。
        # 漏采概率：理论上 0%（除非 Steam API 真出错）。
        # =====================================================================
        if (
            (posted_after is not None or posted_before is not None)
            and last_cursor
            and (auto or fetched < max_count)  # 非自动：max_count 触顶时验证页无意义
        ):
            verify_params = {
                "json": 1,
                "filter": filter,
                "language": language or "all",
                "cursor": last_cursor,
                "num_per_page": 100,
                "purchase_type": "all",
                "day_range": day_range,
            }
            try:
                verify_resp = self.session.get(url, params=verify_params, timeout=30)
                verify_resp.raise_for_status()
                verify_data = verify_resp.json()
                verify_reviews = verify_data.get("reviews", [])
                # 检查验证页里是否还有"时间内"的评论
                leftover_in_window = []
                for r in verify_reviews:
                    ts = r.get("timestamp_created")
                    if _passes_time_filter(ts):
                        leftover_in_window.append(r)
                if leftover_in_window:
                    # 翻页过早停止：把当前 cursor 之后的"时间内"评论继续 yield
                    log.warning(
                        f"⚠️ 翻页验证发现漏采风险：还有 {len(leftover_in_window)} 条时间窗内评论，"
                        f"继续拉取（建议增加 max_count 或检查 Steam 协议变化）"
                    )
                    # 先 yield 验证页本身的漏采评论
                    for r in leftover_in_window:
                        src_id = str(r.get("recommendationid", ""))
                        if not src_id or src_id in seen_source_ids:
                            continue
                        seen_source_ids.add(src_id)
                        yield self._to_raw(target_id, r, fetch_metadata=fetch_metadata)
                        fetched += 1
                        if not auto and fetched >= max_count:
                            break
                    # 兜底再翻 2 页（防止漏采扩散）
                    rescue_cursor = verify_data.get("cursor") or last_cursor
                    for attempt in range(2):
                        if not rescue_cursor:
                            break
                        rescue_params = {
                            "json": 1,
                            "filter": filter,
                            "language": language or "all",
                            "cursor": rescue_cursor,
                            "num_per_page": 100,
                            "purchase_type": "all",
                            "day_range": day_range,
                        }
                        rescue_resp = self.session.get(url, params=rescue_params, timeout=30)
                        rescue_resp.raise_for_status()
                        rescue_data = rescue_resp.json()
                        rescue_reviews = rescue_data.get("reviews", [])
                        page_added = 0
                        for r in rescue_reviews:
                            ts = r.get("timestamp_created")
                            if not _passes_time_filter(ts):
                                continue
                            src_id = str(r.get("recommendationid", ""))
                            if not src_id or src_id in seen_source_ids:
                                continue
                            seen_source_ids.add(src_id)
                            yield self._to_raw(target_id, r, fetch_metadata=fetch_metadata)
                            fetched += 1
                            page_added += 1
                            if not auto and fetched >= max_count:
                                break
                        if page_added == 0:
                            break  # 兜底页也无新数据，停
                        rescue_cursor = rescue_data.get("cursor")
            except Exception as e:
                log.warning(f"验证页拉取失败（不影响已采集数据）：{e}")

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
        # Steam 时间戳是 Unix 秒；统一落库为 naive UTC（与 fetched_at/_utcnow 口径一致）
        ts = review.get("timestamp_created")
        posted_at = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) if ts else None

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
