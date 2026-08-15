"""B 站 API 探针：验证视频信息/互动统计/评论/弹幕接口可访问性（2026-08-11）

用法：
    python scripts/dev/probe_bilibili.py <bvid>
"""
from __future__ import annotations

import json
import sys
import time

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

FULL_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",  # 不用 br（requests 无 brotli 解压）
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Connection": "keep-alive",
}

session = requests.Session()
session.headers.update(FULL_HEADERS)


def fetch_buvid() -> None:
    """用官方指纹接口获取 buvid3+buvid4（多数 API 的隐性前置）"""
    r = session.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=10)
    if r.status_code == 200:
        d = r.json().get("data") or {}
        session.cookies.set("buvid3", d.get("b_3", ""), domain=".bilibili.com")
        session.cookies.set("buvid4", d.get("b_4", ""), domain=".bilibili.com")
        print(f"[buvid] buvid3={d.get('b_3','')[:12]}... buvid4={d.get('b_4','')[:12]}...")
    else:
        print(f"[buvid] ❌ HTTP {r.status_code}")


def api_get(name: str, url: str, params: dict | None = None) -> dict:
    """请求 B 站 JSON API 并打印 code/msg + 关键字段"""
    t0 = time.time()
    try:
        r = session.get(url, params=params, timeout=15)
        elapsed = f"{time.time()-t0:.1f}s"
        if r.status_code != 200:
            print(f"[{name}] ❌ HTTP {r.status_code} ({elapsed})")
            return {}
        data = r.json()
        code, msg = data.get("code"), data.get("message", data.get("msg", ""))
        if code != 0:
            print(f"[{name}] ⚠️ code={code} msg={msg} ({elapsed})")
            return data
        print(f"[{name}] ✅ code=0 ({elapsed})")
        return data.get("data") or {}
    except Exception as e:
        print(f"[{name}] ❌ 异常: {e}")
        return {}


def main() -> None:
    bvid = sys.argv[1] if len(sys.argv) > 1 else "BV1GJ411x7h7"
    fetch_buvid()

    # 1. 视频信息 + 互动统计（view 接口：标题/时间/分区/UP主/简介/stat）
    view = api_get(
        "view", "https://api.bilibili.com/x/web-interface/view",
        {"bvid": bvid},
    )
    if view:
        print(f"  标题: {view.get('title')}")
        print(f"  分区: {view.get('tname')} (tid={view.get('tid')})")
        print(f"  发布: {time.strftime('%Y-%m-%d %H:%M', time.localtime(view.get('pubdate', 0)))}")
        owner = view.get("owner") or {}
        print(f"  UP主: {owner.get('name')} (mid={owner.get('mid')})")
        print(f"  简介: {(view.get('desc') or '')[:60]}")
        stat = view.get("stat") or {}
        print(f"  stat: 播放={stat.get('view')} 弹幕={stat.get('danmaku')} "
              f"评论={stat.get('reply')} 收藏={stat.get('favorite')} "
              f"投币={stat.get('coin')} 分享={stat.get('share')} "
              f"点赞={stat.get('like')}")
        print(f"  aid={view.get('aid')} cid={view.get('cid')}")

        # 2. 标签（tags 接口）
        tags = api_get(
            "tags", "https://api.bilibili.com/x/tag/archive/tags",
            {"bvid": bvid},
        )
        if isinstance(tags, list):
            names = [t.get("tag_name") for t in tags[:10]]
            print(f"  标签({len(tags)}): {names}")

        # 3. 评论（reply 接口：type=1 视频评论）
        reply = api_get(
            "reply", "https://api.bilibili.com/x/v2/reply",
            {"type": 1, "oid": view.get("aid"), "pn": 1, "ps": 5, "sort": 0},
        )
        if reply and isinstance(reply.get("replies"), list):
            for c in reply["replies"][:3]:
                print(f"  └ 评论: {c.get('ctime', 0)} | like={c.get('like')} "
                      f"rcount={c.get('rcount')} | {str(c.get('content', {}).get('message', ''))[:40]}")
                sub = c.get("replies") or []
                if sub:
                    print(f"     └ 楼中楼 {len(sub)} 条, 首条: {str(sub[0].get('content', {}).get('message', ''))[:30]}")

        # 4. 弹幕（老接口 list.so 返回 XML；新接口 seg.so 需 WBI）
        try:
            r = session.get(
                "https://api.bilibili.com/x/v1/dm/list.so",
                params={"oid": view.get("cid")}, timeout=15,
            )
            txt = r.text
            dcount = txt.count("<d ")
            print(f"[danmaku] ✅ list.so HTTP {r.status_code}, 本页弹幕 {dcount} 条 "
                  f"(内容类型: {'XML' if '<d ' in txt else r.headers.get('content-type')})")
            if dcount:
                import re
                m = re.search(r'p="([^"]+)"', txt)
                if m:
                    p = m.group(1).split(",")
                    print(f"  弹幕属性示例: p=时间[{p[0]}s] 模式[{p[1]}] 字号[{p[2]}] "
                          f"颜色[{p[3]}] 发送时间戳[{p[4]}] pool[{p[5]}] 用户hash[{p[6]}]")
        except Exception as e:
            print(f"[danmaku] ❌ 异常: {e}")


if __name__ == "__main__":
    main()
