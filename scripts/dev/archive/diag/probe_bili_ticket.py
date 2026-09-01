"""bili_ticket + WBI 签名解决 UP 主空间接口 -352 风控"""
from __future__ import annotations

import hashlib
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

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def get_wbi_keys() -> tuple[str, str]:
    r = s.get("https://api.bilibili.com/x/web-interface/nav", timeout=10)
    data = r.json().get("data") or {}
    img = (data.get("wbi_img") or {}).get("img_url", "")
    sub = (data.get("wbi_img") or {}).get("sub_url", "")
    return img.rsplit("/", 1)[-1].split(".")[0][:32], sub.rsplit("/", 1)[-1].split(".")[0][:32]


def get_bili_ticket() -> str | None:
    """GenWebTicket：POST 拿 bili_ticket"""
    ts = int(time.time())
    hexsign = hashlib.md5(f"{ts}XgwSnGZ1p".encode()).hexdigest()
    body = {
        "key_id": "ec02",
        "hexsign": hexsign,
        "context[ts]": str(ts),
        "context[device_name]": "你的名字",
        "csrf": "",
    }
    r = s.post(
        "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    try:
        j = r.json()
        data = j.get("data") or {}
        ticket = data.get("ticket")
        if ticket:
            s.cookies.set("bili_ticket", ticket, domain=".bilibili.com")
            s.cookies.set("bili_ticket_expires", str(data.get("expires_in", 2592000)), domain=".bilibili.com")
        print(f"[ticket] code={j.get('code')} nav_status={data.get('nav_status')} ticket={'OK' if ticket else 'None'}")
        return ticket
    except Exception as e:
        print(f"[ticket] 失败: {e}, body={r.text[:100]}")
        return None


def enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    mixin = "".join((img_key + sub_key)[i] for i in MIXIN_KEY_ENC_TAB)
    params = dict(params)
    params["wts"] = int(time.time())
    params = dict(sorted(params.items()))
    params = {k: "".join(ch for ch in str(v) if ch not in "!'()*") for k, v in params.items()}
    query = urllib.parse.urlencode(params)
    params["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return params


def main() -> None:
    r = s.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=10)
    d = r.json().get("data") or {}
    s.cookies.set("buvid3", d.get("b_3", ""), domain=".bilibili.com")
    s.cookies.set("buvid4", d.get("b_4", ""), domain=".bilibili.com")
    img_key, sub_key = get_wbi_keys()
    get_bili_ticket()

    mid = 42723572
    params = enc_wbi({
        "mid": mid, "ps": 30, "tid": 0, "pn": 1, "keyword": "",
        "order": "pubdate", "platform": "web", "web_location": 1550101,
        "dm_img_list": "[]", "dm_img_str": "V2ViR0wtNTAtMC0xMTgtMC0x", 
        "dm_cover_img_str": "QU5HTEUgKEdvb2dsZSBMbGMsIEdsZW9ybWUgVHJhY2tpbmcgU2VydmVyLCBDaHJvbWUgMTI2LjAuMC4wIFdpbmRvd3M=",
        "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
    }, img_key, sub_key)

    r2 = s.get("https://api.bilibili.com/x/space/wbi/arc/search", params=params, timeout=15)
    j = r2.json()
    print(f"[arc/search] code={j.get('code')} msg={j.get('message')}")
    data = j.get("data") or {}
    vlist = (data.get("list") or {}).get("vlist") or []
    print(f"  视频总数: {(data.get('page') or {}).get('count')}, 本页 {len(vlist)} 条")
    for v in vlist[:5]:
        print(f"  ├ {v.get('title')[:35]} | bvid={v.get('bvid')} | 播放={v.get('play')}")
    # 也试一次不加密 wbi 的（对照）
    if not vlist:
        r3 = s.get("https://api.bilibili.com/x/space/arc/search", params={
            "mid": mid, "ps": 30, "tid": 0, "pn": 1, "order": "pubdate",
        }, timeout=15)
        j3 = r3.json()
        print(f"[arc/search 无wbi对照] code={j3.get('code')} msg={j3.get('message')}")


if __name__ == "__main__":
    main()
