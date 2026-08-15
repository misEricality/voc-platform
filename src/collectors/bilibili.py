"""B 站采集器（BilibiliCollector · 2026-08-13）

规格依据：docs/architecture/BILIBILI_COLLECTION.md（已定稿）

核心设计：
- 采集定位：发布满 7 天后的「口碑稳态快照」，非持续监控
- 评论决策分支：评论数 ≤ T(2000) 全量翻页；> T 抽样 K=1000（点赞 top-600 + 最新 400）
- 弹幕永远抽样：progress 时间轴均匀取 ≤3000 条（双时间戳 progress+posted_at）
- 评论者画像：reply 接口 member 自带 level/vip/sex/official，零成本入 extra_json
- 前置条件：buvid3+buvid4 + 完整浏览器头；评论采集需登录 cookie（SESSDATA，热门视频匿名只给 3 条）

合规：仅公开接口、低频（≥1.2s 间隔）、弹幕只落 user_hash。
"""

from __future__ import annotations

import os
import random
import re
import time
from datetime import datetime
from typing import Iterator

import requests

from .base import BaseCollector, RawComment

# B 站完整浏览器头（缺失任一关键头可能触发 412）
FULL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",  # 不带 br（requests 无 brotli 解压会乱码）
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

# 防抖 XML 弹幕属性解析：<d p="progress,mode,fontsize,color,ts,pool,hash,uid">text</d>
DM_PATTERN = re.compile(r'<d p="([^"]+)">(.*?)</d>', re.DOTALL)


class BilibiliCollector(BaseCollector):
    """B 站采集器：视频信息 + 评论（全量/抽样）+ 弹幕（分片抽样）"""

    platform = "bilibili"

    # 策略参数（文档 4.2，T 待实测校准）
    T = 2000          # 评论数阈值：>T 触发抽样
    K = 1000          # 抽样绝对数量（每视频恒定，保证跨视频可比）
    TOP_N = 600       # 抽样：sort=2 点赞序取 top-N
    RECENT_N = 400    # 抽样：sort=0 时间序取最新 N
    PAGE_SIZE = 20    # reply 每页条数（ps 上限 20/49，用 20 稳）
    DANMAKU_LIMIT = 3000  # 弹幕单视频上限（分片抽样后）
    REQUEST_INTERVAL = 1.2  # 请求间隔（秒，防风控）

    def __init__(self, sessdata: str | None = None, **kwargs):
        super().__init__(**kwargs)
        # SESSDATA：热门视频评论必须登录态，否则匿名降级只给 3 条
        self.sessdata = sessdata or os.getenv("BILIBILI_SESSDATA")
        self._http_session: requests.Session | None = None

    # ==================== 底层 HTTP ====================

    def _http(self) -> requests.Session:
        """懒构建会话：buvid3/4 + 可选 SESSDATA + 完整浏览器头"""
        if self._http_session is not None:
            return self._http_session
        s = requests.Session()
        s.headers.update(FULL_HEADERS)
        # 1. buvid3 + buvid4（多数 API 的隐性前置，缺则 412）
        try:
            r = s.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=10)
            d = r.json().get("data") or {}
            s.cookies.set("buvid3", d.get("b_3", ""), domain=".bilibili.com")
            s.cookies.set("buvid4", d.get("b_4", ""), domain=".bilibili.com")
        except Exception:
            pass  # spi 失败不致命，后续请求可能 412 由调用方处理
        # 2. 登录 cookie（评论采集必需）
        if self.sessdata:
            s.cookies.set("SESSDATA", self.sessdata, domain=".bilibili.com")
        self._http_session = s
        return s

    def _get_json(self, url: str, params: dict) -> dict:
        """GET JSON API + 频率控制 + 风控异常抛出"""
        self._throttle()
        r = self._http().get(url, params=params, timeout=15)
        try:
            j = r.json()
        except ValueError:
            raise RuntimeError(f"非 JSON 响应 HTTP {r.status_code}（疑似风控）")
        code = j.get("code")
        if code != 0:
            raise RuntimeError(
                f"B 站 API 错误 code={code} msg={j.get('message', j.get('msg', ''))}"
            )
        return j.get("data") or {}

    def _throttle(self) -> None:
        """请求间隔：固定 1.2s + 随机 0~0.5s（防 IP 风控）"""
        time.sleep(self.REQUEST_INTERVAL + random.random() * 0.5)

    # ==================== 视频信息 ====================

    def fetch_video_info(self, target_id: str) -> dict:
        """获取视频元数据（view + tags 合并）

        Args:
            target_id: bvid（BV...）或 aid（纯数字）

        Returns:
            {bvid, aid, cid, title, tid, tname, pubdate, owner, desc, stat, tags}
        """
        params = {"bvid": target_id} if target_id.startswith("BV") else {"aid": target_id}
        view = self._get_json("https://api.bilibili.com/x/web-interface/view", params)
        bvid = view.get("bvid") or target_id
        # tags
        try:
            tags = self._get_json(
                "https://api.bilibili.com/x/tag/archive/tags", {"bvid": bvid}
            )
            tag_names = [t.get("tag_name") for t in tags if isinstance(t, dict)]
        except Exception:
            tag_names = []
        return {
            "bvid": bvid,
            "aid": view.get("aid"),
            "cid": view.get("cid"),
            "title": view.get("title"),
            "tid": view.get("tid"),
            "tname": view.get("tname"),
            "pubdate": view.get("pubdate"),
            "owner": view.get("owner"),
            "desc": view.get("desc"),
            "stat": view.get("stat"),
            "tags": tag_names,
        }

    # ==================== 评论（全量/抽样） ====================

    def fetch_comments(
        self,
        target_id: str,
        *,
        max_count: int = 100,
        language: str | None = None,
        **kwargs,
    ) -> Iterator[RawComment]:
        """拉取视频评论（决策分支：≤T 全量 / >T 抽样）

        Args:
            target_id: bvid 或 aid
            max_count: 上限保护（默认 K=1000；≤T 全量时也受此限制）

        Yields:
            RawComment（extra 含 aid/bvid/profile 画像）
        """
        info = self.fetch_video_info(target_id)
        aid = info["aid"]
        if not aid:
            raise RuntimeError(f"无法获取 aid: {target_id}")

        reply_count = (info.get("stat") or {}).get("reply") or 0
        if reply_count <= self.T:
            # 全量分支：点赞序翻页到底（或 max_count 上限）
            yield from self._fetch_reply_pages(aid, sort=2, limit=max_count)
        else:
            # 抽样分支：点赞 top-600 + 最新 400（文档 4.2）
            seen: set[int] = set()
            top_n = min(self.TOP_N, max_count)
            recent_n = min(self.RECENT_N, max(0, max_count - top_n))
            yield from self._fetch_reply_pages(
                aid, sort=2, limit=top_n, seen=seen
            )
            yield from self._fetch_reply_pages(
                aid, sort=0, limit=recent_n, seen=seen
            )

    def _fetch_reply_pages(
        self,
        aid: int,
        *,
        sort: int,
        limit: int,
        seen: set[int] | None = None,
    ) -> Iterator[RawComment]:
        """按排序翻页拉评论（sort=2 点赞序 / sort=0 时间序）"""
        seen = seen if seen is not None else set()
        fetched = 0
        pn = 1
        while fetched < limit:
            data = self._get_json(
                "https://api.bilibili.com/x/v2/reply",
                {"type": 1, "oid": aid, "pn": pn, "ps": self.PAGE_SIZE, "sort": sort},
            )
            replies = data.get("replies") or []
            if not replies:
                break  # 翻页到底（或匿名降级截断）
            for c in replies:
                rpid = c.get("rpid")
                if not rpid or rpid in seen:
                    continue
                seen.add(rpid)
                fetched += 1
                yield self._to_raw(c, aid)
            pn += 1

    def _to_raw(self, c: dict, aid: int) -> RawComment:
        """reply 条目 → RawComment（extra 带画像）"""
        member = c.get("member") or {}
        content = (c.get("content") or {}).get("message") or ""
        ctime = c.get("ctime")
        profile = {
            "uname": member.get("uname"),
            "level": (member.get("level_info") or {}).get("current_level"),
            "vip": member.get("vip"),
            "sex": member.get("sex"),
            "official": member.get("official_verify"),
        }
        return RawComment(
            platform="bilibili",
            source_id=str(c.get("rpid")),
            content=content,
            author=member.get("uname"),
            author_id=str(member.get("mid")) if member.get("mid") else None,
            rating=None,  # B 站无评分
            language="zh-CN",
            likes=c.get("like"),
            replies=c.get("rcount"),
            posted_at=datetime.fromtimestamp(ctime) if ctime else None,
            extra={"aid": aid, "profile": profile},
        )

    # ==================== 弹幕（分片抽样） ====================

    def fetch_danmaku(self, cid, *, limit: int | None = None) -> list[dict]:
        """拉取弹幕（list.so XML 全量 → progress 均匀分片抽样）

        Args:
            cid: 视频 cid（分 P 弹幕池 id）
            limit: 抽样上限（默认 DANMAKU_LIMIT=3000）

        Returns:
            [{content, progress(秒), mode, color, user_hash, posted_at(datetime)}]
        """
        limit = limit or self.DANMAKU_LIMIT
        self._throttle()
        r = self._http().get(
            "https://api.bilibili.com/x/v1/dm/list.so", params={"oid": cid}, timeout=20
        )
        if r.status_code != 200:
            raise RuntimeError(f"弹幕接口 HTTP {r.status_code}")
        # 必须 content.decode('utf-8')：r.text 会因响应头缺 charset 按 latin-1 解码导致乱码
        text = r.content.decode("utf-8", errors="replace")
        matches = DM_PATTERN.findall(text)
        items = []
        for attr_str, text in matches:
            parts = attr_str.split(",")
            try:
                progress = int(float(parts[0]))
            except (ValueError, IndexError):
                progress = 0
            try:
                ts = int(float(parts[4]))
            except (ValueError, IndexError):
                ts = None
            items.append(
                {
                    "content": text.strip(),
                    "progress": progress,
                    "mode": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None,
                    "color": int(parts[3]) if len(parts) > 3 and parts[3].lstrip('-').isdigit() else None,
                    "user_hash": parts[6] if len(parts) > 6 else None,
                    "posted_at": datetime.fromtimestamp(ts) if ts else None,
                }
            )
        # progress 时间轴均匀分片抽样（保持时间分布，不截头截尾）
        if len(items) > limit:
            step = len(items) / limit
            sampled = [items[int(i * step)] for i in range(limit)]
            return sampled
        return items


if __name__ == "__main__":
    # 冒烟测试
    import sys
    from dotenv import load_dotenv

    load_dotenv()
    bvid = sys.argv[1] if len(sys.argv) > 1 else "BV1UpwaeNESx"
    c = BilibiliCollector()
    info = c.fetch_video_info(bvid)
    print(f"视频: {info['title']} | UP主: {(info.get('owner') or {}).get('name')}")
    print(f"  评论 {info['stat']['reply']} | 弹幕 {info['stat']['danmaku']} | 播放 {info['stat']['view']}")
    print(f"  tags: {info.get('tags')}")
