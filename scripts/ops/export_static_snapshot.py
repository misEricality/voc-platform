"""方案③ 静态快照导出（EdgeOne Pages 作品集门面）

把 data/voc.db 的聚合结果导出为预生成 JSON 快照 + 完整静态站点目录，
配合 product/web 的 STATIC_SNAPSHOT shim（api.js 静态模式）直接发布到 EdgeOne Pages。

设计约束（DEPLOYMENT_OPTIONS.md 决策 6）：只放聚合结果，不放原始评论全量；
列表类端点只导出前 N 页样本（--list-pages，默认 3 页 × 10 条）。

导出内容 = manifest.json（端点+参数→文件映射）+ api/*.json + covers/ + 前端静态资源
+ index.html（静态版入口：隐藏系统管理导航、注入快照开关与生成时间）。

使用：
    # 手动导出（发布见 publish_static_snapshot.ps1）
    .venv-ml\\Scripts\\python.exe scripts\\ops\\export_static_snapshot.py

    # 自定义库/输出/列表页数
    python scripts/ops/export_static_snapshot.py --db-path data/voc.db \
        --out data/exports/snapshot --list-pages 3

被 scripts/ops/daily_incremental_collect.py 在采集成功后调用（--skip-snapshot 可跳过）。

口径对齐说明（与前端页面逐行核对过）：
- 「近 N 天不含当天」：start = 今天-N 天，end = 昨天（dashboard.js windowParams）
- trends「所有时间」档前端只发 target（实时 API 回落 days=30 语义），快照同样按
  无 start/end 调 service.trends_payload（导出时刻的近 30 天）
- compare「同期」窗口依赖运行时动态计算 → 静态版强制「累计」口径（见 compare.js 静态守卫）
- games/meta 复用 service.games_meta_payload(spawn_refresh=False) 纯读，不起后台线程
- 弹幕桶 samples 后端为 random.sample → 导出前固定随机种子，保证幂等
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

# 让脚本从项目根直接运行
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.snapshot_export")

WEB_DIR = ROOT / "product" / "web"

# 静态版缓存串：每次导出独立 bump（精确到分钟——同一天多次发布 URL 也不同，
# 避免 EdgeOne CDN 返回上一版的 main.js 等资源），与实时版（v=20260906c 等）互不干扰
STAMP_FMT = "%Y%m%d%H%M"


# ---------- 可测试的纯函数 ----------

def compute_windows(today: date) -> dict[str, dict[str, str]]:
    """复刻 dashboard.js windowParams()：「近 N 天」不含当天（本地时区日期）。

    返回 {"all": {}, "30d": {start,end}, "7d": {...}, "1d": {...}}
    """
    def dstr(offset: int) -> str:
        return (today - timedelta(days=offset)).isoformat()

    return {
        "all": {},
        "30d": {"start": dstr(30), "end": dstr(1)},
        "7d": {"start": dstr(7), "end": dstr(1)},
        "1d": {"start": dstr(1), "end": dstr(1)},
    }


def canonical_query(params: dict[str, Any]) -> str:
    """参数字典 → 确定性字符串（键排序；值转字符串）。空值/None 参数一律剔除。"""
    clean = {
        k: str(v) for k, v in sorted(params.items())
        if v is not None and str(v) != ""
    }
    return "&".join(f"{k}={v}" for k, v in clean.items())


def clean_params(params: dict[str, Any]) -> dict[str, str]:
    """剔除空值/None + 全部转字符串（与前端 URLSearchParams 删空值行为一致）"""
    return {
        k: str(v) for k, v in params.items()
        if v is not None and str(v) != ""
    }


def file_name_for(endpoint: str, params: dict[str, Any]) -> str:
    """端点 + 参数 → 快照文件相对路径（api/<端点名>__<hash8>.json）

    端点名取 /api 后的第一段（如 /api/danmaku/bilibili:video:1 → danmaku），
    避免把含 `:` 的路径参数带进 Windows 文件名。
    """
    parts = endpoint.strip("/").split("/")
    name = parts[1] if len(parts) > 1 else (parts[0] or "root")
    h = hashlib.sha1(canonical_query(params).encode("utf-8")).hexdigest()[:8]
    return f"api/{name}__{h}.json"


def parse_query(qs: str) -> dict[str, str]:
    """查询串 → 参数字典（解码 + 删空值），镜像 JS shim 的 URLSearchParams 行为"""
    out: dict[str, str] = {}
    for part in qs.split("&"):
        if not part:
            continue
        k, _, v = part.partition("=")
        from urllib.parse import unquote_plus
        k, v = unquote_plus(k), unquote_plus(v)
        if v != "":
            out[k] = v
    return out


def match_route(routes: list[dict], path: str, params: dict[str, str]) -> dict | None:
    """JS shim 同款匹配：端点相等 + 参数键值集完全一致（多传/少传都算未收录）。

    先清洗请求参数（删空值，与 JS shim URLSearchParams 行为一致）；
    值为 `*` 的路由参数是通配符：匹配任意**非空**值（用于 /api/games/meta ——
    dashboard 与 compare 发送的 targets 串排序不同但内容等价）。
    """
    params = clean_params(params)
    for r in routes:
        if r["path"] != path:
            continue
        rparams = r["params"]
        if set(rparams) != set(params):
            continue
        if all(
            (rv == "*" and params[k]) or rv == params[k]
            for k, rv in rparams.items()
        ):
            return r
    return None


def build_static_index(raw_html: str, meta: dict, stamp: str) -> str:
    """由实时版 index.html 生成静态版入口：
    1) 标题改为静态快照标识
    2) 移除「系统管理」导航（admin + data）
    3) 移除 data.js / admin.js 脚本引用
    4) 缓存串统一替换为快照 stamp（与实时版缓存互不干扰）
    5) 注入 STATIC_SNAPSHOT 开关 + SNAPSHOT_META（内联，零额外请求）
    """
    html = raw_html
    html = html.replace("Lynx — 实时口碑看板", "Lynx — 口碑看板（静态快照）")

    # 移除包含 #/admin 的 nav-drop 块（外层 div 套内层 nav-menu div，非贪婪到双 </div>）
    html = re.sub(
        r'<div class="nav-drop">\s*<a href="#/admin".*?</div>\s*</div>\s*',
        "",
        html,
        flags=re.DOTALL,
    )

    # 移除 data.js / admin.js 脚本（路由表缺 admin/data → 旧链接自动回落 dashboard）
    html = re.sub(
        r'<script src="src/pages/(?:data|admin)\.js[^"]*"></script>\s*',
        "",
        html,
    )

    # 缓存串统一为快照 stamp
    html = re.sub(r"\?v=[0-9]{8}[a-z]?", f"?v={stamp}", html)

    # 注入静态开关与元信息（置于 api.js 之前，保证 shim 初始化时开关已就绪）
    bootstrap = (
        "<script>window.STATIC_SNAPSHOT = true; "
        f"window.SNAPSHOT_META = {json.dumps(meta, ensure_ascii=False)};</script>\n"
    )
    html = html.replace(
        '<script src="src/api.js', f"{bootstrap}<script src=\"src/api.js"
    )
    return html


# ---------- 数据导出 ----------

class SnapshotBuilder:
    """收集 manifest 路由 + 落盘 JSON。路由按「前端实际发送的参数」注册。"""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.api_dir = out_dir / "snapshot" / "api"
        self.routes: list[dict] = []
        self.count = 0

    def add(self, endpoint: str, params: dict[str, Any], data: Any) -> str:
        """注册一条路由并写 JSON。同参数重复注册（如 games/meta 两种排序入参）
        复用同一文件，不重复落盘。"""
        params = clean_params(params)
        hit = match_route(self.routes, endpoint, params)
        if hit:
            return hit["file"]
        rel = file_name_for(endpoint, params)
        path = self.out_dir / "snapshot" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        self.routes.append({"path": endpoint, "params": params, "file": rel})
        self.count += 1
        return rel

    def write_manifest(self, meta: dict) -> None:
        manifest = {
            "version": 1,
            "generated_at": meta["generated_at"],
            "generator": "scripts/ops/export_static_snapshot.py",
            "snapshot_meta": meta,
            "routes": self.routes,
        }
        mpath = self.out_dir / "snapshot" / "manifest.json"
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )


def export_snapshot(
    db_path: Path,
    out_dir: Path,
    *,
    list_pages: int = 3,
    page_size: int = 10,
) -> dict:
    """执行完整导出，返回统计信息（供调用方记日志/写摘要）"""
    from src.api import service
    from src.storage.db import init_db

    now_local = datetime.now().astimezone()
    generated_at = now_local.strftime("%Y-%m-%d %H:%M")
    stamp = now_local.strftime(STAMP_FMT)

    # 1) WAL checkpoint：保证读取一致性 + 随后拷贝的 covers 与数据同源
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    log.info("WAL checkpoint 完成：%s", db_path)

    engine, SessionLocal = init_db(f"sqlite:///{db_path}")

    # 2) 输出目录整体重建（幂等：不留上次残留）
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    b = SnapshotBuilder(out_dir)

    with SessionLocal() as s:
        # ---- 共享端点 ----
        steam_targets = service.list_targets_payload(s, "steam", monitored=True)
        tids = [t["target_id"] for t in steam_targets]
        b.add("/api/targets", {"platform": "steam", "monitored": "true"}, steam_targets)
        b.add("/api/topics/tree", {}, service.topic_tree_payload())

        meta_payload = service.games_meta_payload(s, tids, spawn_refresh=False)
        # dashboard（/api/targets 响应序）与 compare（JS localeCompare 名称序）发送的
        # targets 串排序不同但内容等价 → 注册通配路由，两者命中同一文件
        b.add("/api/games/meta", {"targets": "*"}, meta_payload)

        # ---- Steam 三页：游戏 × 时间窗 × 颗粒度 ----
        # 仅导出有数据的游戏：overview_payload 对零数据返回 None（实时 API 404），
        # 快照若落 null 文件会让静态页直接崩溃 → 零数据目标跳过（shim 未收录 → 明确提示）
        steam_with_data = [
            t["target_id"] for t in steam_targets
            if service.overview_payload(s, t["target_id"], grain="comment") is not None
        ]
        skipped = [t["target_id"] for t in steam_targets
                   if t["target_id"] not in steam_with_data]
        if skipped:
            log.info("跳过零数据 Steam 目标 %s 个：%s", len(skipped), ", ".join(skipped))
        windows = compute_windows(date.today())
        for tid in steam_with_data:
            for key, w in windows.items():
                wparams = {"target": tid, **w}
                for grain in ("comment", "opinion"):
                    b.add(
                        "/api/overview",
                        {**wparams, "grain": grain},
                        service.overview_payload(s, tid, start=w.get("start"),
                                                 end=w.get("end"), grain=grain),
                    )
                    b.add(
                        "/api/topics",
                        {**wparams, "level": "L1", "grain": grain, "full": "true"},
                        service.topics_payload(
                            s, tid, level="L1", grain=grain,
                            start=w.get("start"), end=w.get("end"), full=True,
                        ),
                    )
                # trends：所有时间档前端只发 target（服务端回落 days=30 语义）
                b.add(
                    "/api/trends",
                    wparams,
                    service.trends_payload(
                        s, tid, start=w.get("start"), end=w.get("end")
                    ),
                )
            # 列表样本（前 list_pages 页）：comments（原声，time 序）+ opinions（观点）
            for key, w in windows.items():
                wparams = {"target": tid, **w}
                total_c = None
                total_o = None
                for page in range(1, list_pages + 1):
                    cd = service.comments_payload(
                        s, target_id=tid, page=page, page_size=page_size,
                        start=w.get("start"), end=w.get("end"), grain="comment",
                    )
                    total_c = cd["total"]
                    if page == 1 or cd["items"]:
                        b.add(
                            "/api/comments",
                            {**wparams, "grain": "comment",
                             "page": page, "page_size": page_size},
                            cd,
                        )
                    od = service.opinions_payload(
                        s, target_id=tid, page=page, page_size=page_size,
                        start=w.get("start"), end=w.get("end"),
                    )
                    total_o = od["total"]
                    if page == 1 or od["items"]:
                        b.add(
                            "/api/opinions",
                            {**wparams, "page": page, "page_size": page_size},
                            od,
                        )
                log.info(
                    "  %s [%s]：原声 %s 条 / 观点 %s 条（导出前 %s 页）",
                    tid, key, total_c, total_o, list_pages,
                )

        # ---- compare 专属：L2 观点主题 × 情感极性（累计口径，无时间窗） ----
        for tid in steam_with_data:
            for polar in ("negative", "positive"):
                b.add(
                    "/api/topics",
                    {"target": tid, "level": "L2", "grain": "opinion",
                     "sentiment": polar},
                    service.topics_payload(
                        s, tid, level="L2", grain="opinion", sentiment=polar
                    ),
                )

        # ---- B 站视频看板：逐 fetched 视频 ----
        videos = service.bilibili_videos_payload(s)
        b.add("/api/bilibili/videos", {}, videos)
        for v in videos:
            tid = v["target_id"]
            # 情感环形：前端不带 grain（API 默认 comment）→ 参数就是 {target}
            b.add("/api/overview", {"target": tid},
                  service.overview_payload(s, tid, grain="comment"))
            # L1 双图：full=true + 情感极性
            for polar in ("positive", "negative"):
                b.add(
                    "/api/topics",
                    {"target": tid, "level": "L1", "grain": "comment",
                     "full": "true", "sentiment": polar},
                    service.topics_payload(
                        s, tid, level="L1", grain="comment", sentiment=polar,
                        full=True,
                    ),
                )
            # 原声列表（likes 降序）前 list_pages 页
            for page in range(1, list_pages + 1):
                cd = service.comments_payload(
                    s, target_id=tid, page=page, page_size=page_size,
                    grain="comment", sort="likes",
                )
                if page == 1 or cd["items"]:
                    b.add(
                        "/api/comments",
                        {"target": tid, "grain": "comment", "sort": "likes",
                         "page": page, "page_size": page_size},
                        cd,
                    )
            # 弹幕时间轴（固定随机种子保证幂等；samples 每桶 10 条 ≤15 字）
            # 路径存储 encodeURIComponent 后形态（: → %3A），与 JS shim 发送串一致
            random.seed(hashlib.sha1(tid.encode("utf-8")).hexdigest())
            b.add(f"/api/danmaku/{quote(tid, safe='')}", {},
                  service.danmaku_payload(s, tid))

    engine.dispose()

    # 3) 静态版入口 + 前端资源 + 封面
    meta = {
        "generated_at": generated_at,
        "stamp": stamp,
        "comments": _count_comments(db_path),
        "steam_targets": len(tids),
        "bili_videos": len(videos),
    }
    (out_dir / "index.html").write_text(
        build_static_index((WEB_DIR / "index.html").read_text(encoding="utf-8"),
                           meta, stamp),
        encoding="utf-8",
    )
    _copy_tree(WEB_DIR / "src", out_dir / "src")
    _copy_tree(WEB_DIR / "vendor", out_dir / "vendor")
    covers_src = ROOT / "data" / "covers"
    if covers_src.exists():
        _copy_tree(covers_src, out_dir / "covers")

    b.write_manifest(meta)
    return {
        "generated_at": generated_at,
        "routes": len(b.routes),
        "files": b.count,
        "steam_targets": len(tids),
        "bili_videos": len(videos),
        "out_dir": str(out_dir),
    }


def _count_comments(db_path: Path) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        return int(con.execute("select count(*) from comments").fetchone()[0])
    finally:
        con.close()


def _copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            shutil.copy2(item, target)


def main() -> int:
    ap = argparse.ArgumentParser(description="方案③ 静态快照导出")
    ap.add_argument("--db-path", default=str(ROOT / "data" / "voc.db"))
    ap.add_argument("--out", default=str(ROOT / "data" / "exports" / "snapshot"))
    ap.add_argument("--list-pages", type=int, default=3,
                    help="列表类端点导出的页数（每页 10 条样本）")
    args = ap.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        log.error("DB 不存在：%s", db_path)
        return 2

    stats = export_snapshot(Path(args.db_path), Path(args.out),
                            list_pages=max(1, args.list_pages))
    log.info(
        "快照导出完成：%(routes)s 条路由 / %(files)s 个文件"
        "（Steam %(steam_targets)s 款 + B站 %(bili_videos)s 个视频）→ %(out_dir)s",
        stats,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
