"""一次性脚本：验证 6 款游戏的 Steam appid + 官方中文名"""
import json
import sys

import requests

CANDIDATES = {
    "黑神话·悟空": 2358720,
    "巫师3": 292030,
    "文明6": 289070,
    "底特律·变人": 1222140,
    "33号远征队": 1903340,
    "星际拓荒": 753640,
}

print(f"{'用户输入名':<20} {'appid':<10} {'官方中文名（Steam 返回）':<40} {'匹配？'}")
print("=" * 90)

for user_name, appid in CANDIDATES.items():
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=cn&l=schinese"
    headers = {"User-Agent": "VoC-Platform/0.1", "Accept": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        d = r.json()
        if not d[str(appid)]["success"]:
            print(f"{user_name:<20} {appid:<10} {'(success=False)':<40}")
            continue
        info = d[str(appid)]["data"]
        name = info.get("name", "")
        type_ = info.get("type", "")
        release_date = info.get("release_date", {}).get("date", "")
        # 判断用户输入名是否在官方名里（中文名都是子串匹配）
        # 用户名去标点后子串匹配
        user_short = user_name.replace("·", "").replace(" ", "").replace(":", "").replace("：", "")
        name_short = name.replace("·", "").replace(" ", "").replace(":", "").replace("：", "")
        match = user_short in name_short or name_short in user_short
        print(
            f"{user_name:<20} {appid:<10} {name:<40} {type_:<8} {release_date:<14} {'✓' if match else '⚠️'}"
        )
    except Exception as e:
        print(f"{user_name:<20} {appid:<10} {'ERR: ' + str(e)[:30]:<40}")