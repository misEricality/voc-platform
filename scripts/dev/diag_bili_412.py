"""诊断 B 站 412：完整浏览器头 + 多接口横向对比"""
from __future__ import annotations

import requests

FULL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
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

s = requests.Session()
s.headers.update(FULL_HEADERS)

# 拿 buvid3/4（容错：spi 可能被拦）
r = s.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=10)
print(f"spi: HTTP {r.status_code}, content-type={r.headers.get('content-type')}")
if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
    try:
        d = r.json().get("data") or {}
        s.cookies.set("buvid3", d.get("b_3", ""), domain=".bilibili.com")
        s.cookies.set("buvid4", d.get("b_4", ""), domain=".bilibili.com")
        print(f"  buvid3={d.get('b_3','')[:12]}... buvid4={d.get('b_4','')[:12]}...")
    except Exception:
        print("  spi body 非 JSON")
else:
    print(f"  body 前 60: {r.text[:60]}")

# 测多个接口
tests = [
    ("view(bvid)", "https://api.bilibili.com/x/web-interface/view", {"bvid": "BV1GJ411x7h7"}),
    ("nav(登录态)", "https://api.bilibili.com/x/web-interface/nav", {}),
    ("video_list(推荐)", "https://api.bilibili.com/x/web-interface/index/top/rcmd", {"ps": 1}),
    ("search(搜索)", "https://api.bilibili.com/x/web-interface/search/type", {"search_type": "video", "keyword": "黑神话"}),
    ("www首页", "https://www.bilibili.com/", {}),
    ("player wbi", "https://api.bilibili.com/x/player/wbi/v2", {"bvid": "BV1GJ411x7h7", "cid": 1}),
]

for name, url, params in tests:
    try:
        r = s.get(url, params=params, timeout=12)
        body = r.text[:80].replace("\n", " ")
        print(f"[{name}] HTTP {r.status_code} | {body}")
    except Exception as e:
        print(f"[{name}] ❌ {e}")
