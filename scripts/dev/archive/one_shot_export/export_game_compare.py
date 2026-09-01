"""
游戏对比看板数据导出。

从 Steam 官方接口（appdetails + appreviews）按 appid 拉取：
- 官方中文名（?l=schinese）
- 发行日期（年月日）
- 全部评测总数 + 中文好评评级
- 最近评测原文（schinese，<=200 字）

同时清理数据库中 appid 2012510 命名错误（"动物井" → 官方"风暴之门 Stormgate"）。

Usage:
    python scripts/dev/export_game_compare.py
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "voc.db"
OUT = ROOT / "data" / "exports" / "game_compare_data.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
UA = "Mozilla/5.0"

RATING_ZH = {
    1: "差评", 2: "差评", 3: "褒贬不一", 4: "褒贬不一", 5: "褒贬不一",
    6: "好评", 7: "好评", 8: "特别好评", 9: "好评如潮",
}


def fetch(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def get_app_meta(appid: str) -> dict:
    """中文官方名 + 发行日期（年月日）。"""
    d = fetch(f"https://store.steampowered.com/api/appdetails?appids={appid}&l=schinese")
    app = d[appid]["data"]
    rd = app.get("release_date", {})
    date_str = rd.get("date", "") if isinstance(rd, dict) else str(rd)
    # 拆分 "2024 年 8 月 19 日" → 2024 / 8 / 19
    nums = []
    for token in date_str.replace("年", " ").replace("月", " ").replace("日", " ").split():
        if token.isdigit():
            nums.append(int(token))
    y, m, d_ = (nums + [0, 0, 0])[:3]
    return {
        "name": app["name"],  # Steam 官方中文名（已按 ?l=schinese 返回）
        "release_year": y or None,
        "release_month": m or None,
        "release_day": d_ or None,
        "release_text": f"{y}年{m}月{d_}日" if y and m and d_ else (f"{y}年{m}月" if y and m else date_str),
    }


def get_review_meta(appid: str) -> dict:
    """全部评测数 + 中文好评评级 + 最近评测原文。"""
    # total + 评级
    d = fetch(f"https://store.steampowered.com/appreviews/{appid}?json=1&filter=all&language=schinese&num_per_page=0")
    s = d["query_summary"]
    score = int(s.get("review_score") or 0)
    return {
        "total_reviews": int(s.get("total_reviews") or 0),
        "positive_reviews": int(s.get("total_positive") or 0),
        "review_score": score,
        "rating_zh": RATING_ZH.get(score, "无"),
    }


def get_recent_review(appid: str, limit_chars: int = 200) -> dict | None:
    """拿最新一条 schinese 评测（≤200 字）。"""
    d = fetch(f"https://store.steampowered.com/appreviews/{appid}?json=1&filter=recent&language=schinese&num_per_page=5")
    for r in d.get("reviews", []):
        txt = (r.get("review") or "").strip()
        if not txt:
            continue
        if len(txt) > limit_chars:
            txt = txt[:limit_chars] + "..."
        pts = r.get("author", {}).get("playtime_at_review", 0) or 0
        return {
            "text": txt,
            "voted_up": bool(r.get("voted_up")),
            "timestamp": r.get("timestamp_created", 0),
            "playtime_at_review": pts,
            "playtime_text": f"{pts/60:.1f} 小时" if pts else "未公开",
            "vote_text": "推荐" if r.get("voted_up") else "不推荐",
        }
    return None


def get_wordcloud(appid: str, conn: sqlite3.Connection) -> list[dict]:
    """复用库里 2067 条评论的 jieba 词频（按 appid 过滤）。"""
    import jieba  # 内置
    from collections import Counter
    cur = conn.cursor()
    cur.execute(
        "SELECT content FROM comments WHERE platform='steam' AND target_id=?",
        (f"steam:{appid}",),
    )
    texts = [row[0] for row in cur.fetchall()]
    if not texts:
        return []
    stop = {
        "的", "了", "是", "我", "你", "他", "她", "它", "在", "有", "和", "就", "都", "也", "不",
        "还", "没", "把", "让", "使", "被", "对", "从", "到", "为", "以", "及", "或", "但", "而",
        "呢", "吗", "啊", "哦", "吧", "嗯", "哈", "呵", "嘿", "哇", "哎", "啦", "嘛", "呀",
        "这", "那", "这个", "那个", "这样", "那样", "什么", "怎么", "为什么", "可以", "能够",
        "应该", "觉得", "认为", "感觉", "知道", "看到", "听到", "现在", "以前", "以后", "之后",
        "之前", "时候", "地方", "一些", "一点", "一直", "一下", "一起", "一样", "一直", "一定",
        "玩", "游戏", "就是", "真的", "比较", "太", "很", "非常", "特别", "更", "最", "再",
        "就", "而且", "但是", "所以", "因为", "由于", "如果", "虽然", "尽管", "不过", "只是",
        "一个", "这个", "那种", "没", "自己", "里", "上", "下", "中", "没", "最后", "然后",
        "再", "已经", "还", "又", "只是", "而且", "问题", "很多", "确实", "最后",
    }
    cnt = Counter()
    for t in texts:
        for w in jieba.cut(t):
            w = w.strip()
            if len(w) < 2 or len(w) > 6:
                continue
            if not all("\u4e00" <= ch <= "\u9fff" for ch in w):
                continue
            if w in stop:
                continue
            cnt[w] += 1
    # 过滤游戏名
    # 由调用方传游戏名过滤
    return [{"word": w, "count": c} for w, c in cnt.most_common(40)]


def steam_rating_from_pct(p: float) -> str:
    """Steam 好评评级阈值映射（与主看板 dashboard.js 内 steamRating 一致）。"""
    if p >= 95: return "好评如潮"
    if p >= 90: return "特别好评"
    if p >= 80: return "好评"
    if p >= 70: return "多半好评"
    if p >= 40: return "褒贬不一"
    if p >= 20: return "多半差评"
    return "差评"


def get_local_meta(conn: sqlite3.Connection, appid: str) -> dict:
    """库里本地评论量 + 好评率 + 对应 Steam 评级。"""
    cur = conn.cursor()
    n = cur.execute(
        "SELECT COUNT(*), SUM(rating) FROM comments WHERE platform='steam' AND target_id=?",
        (f"steam:{appid}",),
    ).fetchone()
    total, pos = (n or (0, None))
    total = int(total or 0)
    pos = int(pos or 0)
    pct = round(pos / total * 100, 1) if total else 0.0
    return {
        "local_count": total,
        "local_recommend_pct": pct,
        "local_rating_zh": steam_rating_from_pct(pct),
    }


def get_db_name(conn: sqlite3.Connection, appid: str) -> str | None:
    """游戏名优先读库（extra_meta，与 Streamlit list_targets 同源），缺则返回 None 由调用方回退 appdetails。"""
    cur = conn.cursor()
    row = cur.execute(
        "SELECT extra_meta FROM comments WHERE platform='steam' AND target_id=? AND extra_meta IS NOT NULL LIMIT 1",
        (f"steam:{appid}",),
    ).fetchone()
    if row and row[0]:
        try:
            return (json.loads(row[0]) or {}).get("name") or None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def main() -> None:
    # 1) 列出库内所有 steam appid
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT target_id, COUNT(*) c FROM comments WHERE platform='steam' GROUP BY target_id ORDER BY c DESC"
    ).fetchall()
    print(f"库内 steam 游戏 {len(rows)} 款，开始拉取 Steam 接口...")

    games = []
    for tid, local_count in rows:
        appid = tid.split(":", 1)[1]
        try:
            meta = get_app_meta(appid)
            rev = get_review_meta(appid)
            recent = get_recent_review(appid)
            words = get_wordcloud(appid, conn)
            local = get_local_meta(conn, appid)
            # 游戏名优先读库（与 Streamlit 同源），缺则回退 Steam appdetails
            name = get_db_name(conn, appid) or meta["name"]
            words = [w for w in words if w["word"] not in name and len(w["word"]) >= 2][:30]
            games.append({
                "appid": appid,
                "name": name,                       # 官方中文名
                "release_year": meta["release_year"],
                "release_month": meta["release_month"],
                "release_day": meta["release_day"],
                "release_text": meta["release_text"],
                "total_reviews": rev["total_reviews"],   # 来自 Steam API
                "positive_reviews": rev["positive_reviews"],
                "rating_zh": rev["rating_zh"],
                "review_score": rev["review_score"],
                "local_count": local["local_count"],
                "local_recommend_pct": local["local_recommend_pct"],
                "local_rating_zh": local["local_rating_zh"],
                "recent_review": recent,
                "words": words,
            })
            print(f"  {appid} {name:30s} | {rev['rating_zh']:6s} | 全部 {rev['total_reviews']:>8d} | 发行 {meta['release_text']:20s} | 词 {len(words)}")
        except Exception as e:
            print(f"  {appid} FAIL: {type(e).__name__}: {e}")
            games.append({
                "appid": appid,
                "name": appid,
                "error": str(e),
                "local_count": local_count,
            })

    # 2) 清理数据库命名错误（appid 2012510 = 风暴之门 Stormgate）
    fixed = cur.execute(
        "UPDATE comments SET extra_meta = REPLACE(extra_meta, '\"name\": \"动物井\"', '\"name\": \"风暴之门 Stormgate\"') "
        "WHERE target_id = 'steam:2012510' AND extra_meta LIKE '%动物井%'"
    )
    conn.commit()
    print(f"数据库 animal_well → 风暴之门: {fixed.rowcount} 行")

    payload = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "games": games,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"→ {OUT} ({size_kb:.0f} KB) · {len(games)} 款游戏")


if __name__ == "__main__":
    main()
