"""尝试只用 l=schinese（不带 cc）拉 Steam appdetails，看中文名是否变化"""
import json

import requests

CANDIDATES = {
    "黑神话·悟空": 2358720,
    "巫师3": 292030,
    "文明6": 289070,
    "底特律·变人": 1222140,
    "33号远征队": 1903340,
    "星际拓荒": 753640,
}

for user_name, appid in CANDIDATES.items():
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=schinese"
    try:
        r = requests.get(url, timeout=15)
        d = r.json()
        if not d[str(appid)]["success"]:
            continue
        info = d[str(appid)]["data"]
        name = info.get("name", "")
        print(f"  {appid} l=schinese: {name}")
    except Exception as e:
        print(f"  {appid} ERR: {e}")