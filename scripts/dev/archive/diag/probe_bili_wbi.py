"""B 站 WBI 签名 + UP 主视频列表接口实测（2026-08-11）

WBI 签名（2023 年起 UP 主空间/搜索等接口强制）：
1. nav 接口拿 wbi_img（img_url + sub_url）
2. 取两个 URL 文件名的前 32 位 → img_key / sub_key
3. mixin_key = 按固定打乱表重排 img_key+sub_key
4. 参数 + wts(时间戳) → 按 key 排序 → urlencode → md5 → w_rid
"""
from __future__ import annotations

import hashlib
import re
import time
import urllib.parse

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
s = requests.Session()
s.headers.update({
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-site",
})

# WBI mixin key 打乱表（固定）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def get_wbi_keys() -> tuple[str, str]:
    """从 nav 接口获取 img_key / sub_key"""
    r = s.get("https://api.bilibili.com/x/web-interface/nav", timeout=10)
    data = r.json().get("data") or {}
    img_url = (data.get("wbi_img") or {}).get("img_url", "")
    sub_url = (data.get("wbi_img") or {}).get("sub_url", "")
    # 文件名前 32 位（不含扩展名）
    img_key = img_url.rsplit("/", 1)[-1].split(".")[0][:32]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0][:32]
    return img_key, sub_key


def mixin_key(orig: str) -> str:
    """mixin_key = 按打乱表重排"""
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)


def enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    """参数 + wts → 排序 → urlencode → md5 → w_rid"""
    mixin = mixin_key(img_key + sub_key)
    params = dict(params)
    params["wts"] = int(time.time())
    params = dict(sorted(params.items()))
    # 过滤 value 含 !'()* 的（规范要求）
    params = {k: "".join(ch for ch in str(v) if ch not in "!'()*") for k, v in params.items()}
    query = urllib.parse.urlencode(params)
    w_rid = hashlib.md5((query + mixin).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


def main() -> None:
    # 拿 buvid + wbi keys
    r = s.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=10)
    d = r.json().get("data") or {}
    s.cookies.set("buvid3", d.get("b_3", ""), domain=".bilibili.com")
    s.cookies.set("buvid4", d.get("b_4", ""), domain=".bilibili.com")
    img_key, sub_key = get_wbi_keys()
    print(f"[wbi] img_key={img_key} sub_key={sub_key}")

    # 测试 UP 主：黑神话评测视频的 UP 主（从搜索拿 mid）
    r = s.get("https://api.bilibili.com/x/web-interface/search/type", params={
        "search_type": "video", "keyword": "黑神话 评测", "page": 1,
    }, timeout=15)
    items = (r.json().get("data") or {}).get("result") or []
    mid = items[0].get("mid") if items else None
    print(f"[search] 测试 UP 主 mid={mid} name={items[0].get('author') if items else ''}")

    if not mid:
        print("未找到 UP 主，退出")
        return

    # UP 主视频列表（WBI 签名）
    params = enc_wbi({
        "mid": mid,
        "ps": 30,          # 每页 30
        "tid": 0,          # 全分区
        "pn": 1,
        "keyword": "",
        "order": "pubdate",  # 按发布时间排序
        "platform": "web",
        "web_location": 1550101,
        "dm_img_list": "[]",
    }, img_key, sub_key)

    r2 = s.get("https://api.bilibili.com/x/space/wbi/arc/search", params=params, timeout=15)
    j = r2.json()
    print(f"[arc/search] HTTP {r2.status_code} code={j.get('code')} msg={j.get('message')}")
    data = j.get("data") or {}
    vlist = (data.get("list") or {}).get("vlist") or []
    page = data.get("page") or {}
    print(f"  该 UP 主视频总数: {page.get('count')}, 本页: {len(vlist)}")
    for v in vlist[:5]:
        print(f"  ├ {v.get('title')[:30]} | bvid={v.get('bvid')} | "
              f"播放={v.get('play')} 评论={v.get('comment')} | "
              f"{time.strftime('%Y-%m-%d', time.localtime(v.get('created', 0)))}")
    print(f"  (共 {len(vlist)} 条，其余略)")


if __name__ == "__main__":
    main()
