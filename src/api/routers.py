"""API 路由：公开只读端点 + 管理端点（采集任务 CRUD + 鉴权）

字段约束（WEB_DASHBOARD.md §4.3）：
- Steam：appid/URL 🔒（编辑不可改）；name/language/count 可改；enabled 开关
- BiliBili：bv_id/title/pubdate/due_date 🔒；note 可改；暂停/恢复（paused 状态机）
- 删除：Steam 任意状态可删；B 站 fetched 拒绝（409）
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from src.api.auth import (
    SESSION_ADMIN_KEY,
    admin_configured,
    check_login_rate,
    clear_login_failures,
    client_ip,
    get_session,
    hash_password,
    record_login_failure,
    require_admin,
    verify_password,
)
from src.storage.db import (
    BilibiliQueue,
    CollectTask,
    CollectTaskRepository,
    _utcnow,
)

log = logging.getLogger("voc.api")

public_router = APIRouter(prefix="/api", tags=["public"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


def _ok(data) -> dict:
    return {"ok": True, "data": data}


# ==================== 公开只读端点 ====================

@public_router.get("/targets")
def api_targets(
    platform: str | None = None,
    monitored: bool = False,
    s: Session = Depends(get_session),
):
    from src.api import service

    return _ok(service.list_targets_payload(s, platform, monitored=monitored))


@public_router.get("/games/meta")
def api_games_meta(targets: str, s: Session = Depends(get_session)):
    """游戏元数据（发行日期/Steam 评级/封面；缺行或超 24h 自动刷新，失败不阻塞）"""
    from src.api import service

    tlist = [t.strip() for t in targets.split(",") if t.strip()]
    if not tlist:
        raise HTTPException(422, "targets 不能为空（逗号分隔）")
    return _ok(service.games_meta_payload(s, tlist))


@public_router.get("/overview")
def api_overview(
    target: str,
    start: str | None = None,
    end: str | None = None,
    grain: str = "comment",
    s: Session = Depends(get_session),
):
    from src.api import service

    if grain not in {"comment", "opinion"}:
        raise HTTPException(422, "grain 仅支持 comment/opinion")
    try:
        data = service.overview_payload(s, target, start=start, end=end, grain=grain)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if data is None:
        raise HTTPException(404, f"目标不存在或无数据：{target}")
    return _ok(data)


@public_router.get("/topics/tree")
def api_topic_tree():
    """L1~L3 主题树（config/topics/gaming.yaml，前端树状筛选器数据源）"""
    from src.api import service

    return _ok(service.topic_tree_payload())


@public_router.get("/topics")
def api_topics(
    target: str,
    level: str = "L1",
    grain: str = "opinion",
    sentiment: str | None = None,
    start: str | None = None,
    end: str | None = None,
    full: bool = False,
    s: Session = Depends(get_session),
):
    from src.api import service

    if level not in {"L1", "L2", "L3"}:
        raise HTTPException(422, "level 仅支持 L1/L2/L3")
    if grain not in {"comment", "opinion"}:
        raise HTTPException(422, "grain 仅支持 comment/opinion")
    try:
        return _ok(service.topics_payload(
            s, target, level=level, grain=grain, sentiment=sentiment,
            start=start, end=end, full=full,
        ))
    except ValueError as e:
        raise HTTPException(422, str(e))


@public_router.get("/comments")
def api_comments(
    target: str | None = None,
    page: int = 1,
    page_size: int = 10,
    sentiment: str | None = None,
    topic: str | None = None,
    q: str | None = None,
    start: str | None = None,
    end: str | None = None,
    grain: str = "opinion",
    sort: str = "time",
    s: Session = Depends(get_session),
):
    from src.api import service

    if grain not in {"comment", "opinion"}:
        raise HTTPException(422, "grain 仅支持 comment/opinion")
    if sort not in {"time", "likes"}:
        raise HTTPException(422, "sort 仅支持 time/likes")
    page_size = min(max(1, page_size), 100)
    page = max(1, page)
    try:
        return _ok(service.comments_payload(
            s, target_id=target, page=page, page_size=page_size,
            sentiment=sentiment, topic=topic, q=q, start=start, end=end,
            grain=grain, sort=sort,
        ))
    except ValueError as e:
        raise HTTPException(422, str(e))


@public_router.get("/bilibili/videos")
def api_bilibili_videos(s: Session = Depends(get_session)):
    """B 站视频看板数据源：fetched 视频快照 + 采集量 + 性别分布 + 高光总结"""
    from src.api import service

    return _ok(service.bilibili_videos_payload(s))


@public_router.get("/opinions")
def api_opinions(
    target: str | None = None,
    page: int = 1,
    page_size: int = 10,
    sentiment: str | None = None,
    topic: str | None = None,
    start: str | None = None,
    end: str | None = None,
    s: Session = Depends(get_session),
):
    """观点分页列表（观点粒度看板；每条附所属原声）"""
    from src.api import service

    page_size = min(max(1, page_size), 100)
    page = max(1, page)
    try:
        return _ok(service.opinions_payload(
            s, target_id=target, page=page, page_size=page_size,
            sentiment=sentiment, topic=topic, start=start, end=end,
        ))
    except ValueError as e:
        raise HTTPException(422, str(e))


@public_router.get("/danmaku/{bvid}")
def api_danmaku(bvid: str, s: Session = Depends(get_session)):
    from src.api import service

    return _ok(service.danmaku_payload(s, bvid))


@public_router.get("/compare")
def api_compare(targets: str, level: str = "L1", s: Session = Depends(get_session)):
    from src.api import service

    tlist = [t.strip() for t in targets.split(",") if t.strip()]
    if not tlist:
        raise HTTPException(422, "targets 不能为空（逗号分隔）")
    return _ok(service.compare_payload(s, tlist, level=level))


@public_router.get("/trends")
def api_trends(
    target: str | None = None,
    days: int = 30,
    start: str | None = None,
    end: str | None = None,
    s: Session = Depends(get_session),
):
    from src.api import service

    try:
        return _ok(service.trends_payload(
            s, target, days=min(max(1, days), 365), start=start, end=end,
        ))
    except ValueError as e:
        raise HTTPException(422, str(e))


# ==================== 鉴权 ====================

class LoginBody(BaseModel):
    password: str


@auth_router.post("/login")
def api_login(body: LoginBody, request: Request):
    if not admin_configured():
        raise HTTPException(
            503,
            "管理员未配置：请在 .env 设置 ADMIN_PASSWORD_HASH"
            "（生成：python scripts/ops/hash_admin_password.py <密码>）",
        )
    ip = client_ip(request)
    check_login_rate(ip)  # 对抗审查 P2#9：5 次/5 分钟窗口，超出 429
    if not verify_password(body.password, request.app.state.admin_password_hash):
        record_login_failure(ip)
        raise HTTPException(401, "密码错误")
    clear_login_failures(ip)
    request.session[SESSION_ADMIN_KEY] = True
    return _ok({"role": "admin"})


@auth_router.post("/logout")
def api_logout(request: Request):
    request.session.pop(SESSION_ADMIN_KEY, None)
    return _ok({"logged_out": True})


@auth_router.get("/status")
def api_auth_status(request: Request):
    return _ok({
        "admin_configured": admin_configured(),
        "logged_in": bool(request.session.get(SESSION_ADMIN_KEY)),
    })


# ==================== 任务管理（统一视图） ====================

STEAM_STATUS = {1: "采集中", 0: "已暂停"}
BILI_STATUS = {
    "pending": "待采集",
    "scheduled": "待采集",
    "fetching": "采集中",
    "fetched": "已采集",
    "paused": "已暂停",
    "failed": "采集失败",
}


def _steam_url(appid: str) -> str:
    return f"https://store.steampowered.com/app/{appid}/"


def _bili_url(bv: str) -> str:
    return f"https://www.bilibili.com/video/{bv}/"


def _steam_task_view(t: CollectTask) -> dict:
    return {
        **t.to_dict(),
        "url": t.source_url or _steam_url(t.target_id),
        "status_display": STEAM_STATUS.get(bool(t.enabled), "已暂停"),
    }


def _bili_task_view(q: BilibiliQueue) -> dict:
    return {
        **q.to_dict(),
        "url": _bili_url(q.bv_id),
        "status_display": BILI_STATUS.get(q.status, q.status),
    }


def _parse_steam_appid(raw: str) -> str:
    """支持 store URL 或裸 appid"""
    raw = raw.strip()
    m = re.search(r"store\.steampowered\.com/app/(\d+)", raw)
    if m:
        return m.group(1)
    if re.fullmatch(r"\d+", raw):
        return raw
    raise HTTPException(422, f"无法从输入解析 Steam AppID：{raw}")


def _fetch_steam_game_name(appid: str) -> str | None:
    """Steam appdetails 回填名称（尽力而为，失败返回 None）"""
    try:
        import requests

        r = requests.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": appid, "l": "schinese"},
            timeout=10,
        )
        if r.status_code == 200:
            d = r.json().get(appid) or {}
            if d.get("success") and d.get("data"):
                return d["data"].get("name")
    except Exception as e:  # noqa: BLE001
        log.warning("appdetails 回填失败 appid=%s: %s", appid, e)
    return None


def _bili_lookup(bv: str) -> tuple:
    """复用 queue CLI 的 view 接口识别（pubdate, title）"""
    from src.queue.cli import _lookup_pubdate

    return _lookup_pubdate(bv)


# ---------- backfill 状态追踪（对抗审查 P2#6：可观测性） ----------
# in-memory，单进程；key="{platform}:{target_id}"；最新 job 覆盖旧 job。
_backfill_jobs: dict[str, dict] = {}


def _backfill_status(platform: str, target_id: str, **fields) -> dict:
    key = f"{platform}:{target_id}"
    job = _backfill_jobs.get(key, {})
    job.update(fields)
    job.setdefault("platform", platform)
    job.setdefault("target_id", target_id)
    _backfill_jobs[key] = job
    return job


def _spawn_backfill(platform: str, target_id: str, days: int = 7) -> None:
    """后台线程触发首次采集（近 N 天）；记录状态供 /api/admin/backfill-status 轮询。

    完成后回写 DB：Steam → collect_tasks.last_collected_at；B站 → bilibili_queue
    的 fetched_at/comment_count/status（成功 fetched / 失败保持原态或 failed）。
    """
    started = _utcnow()
    _backfill_status(platform, target_id, status="running", started_at=started.isoformat(),
                     finished_at=None, fetched=None, analyzed=None, error=None)

    def _run():
        try:
            from src.pipeline import run_pipeline
            from src.storage.db import BilibiliQueue, CollectTaskRepository, init_db

            posted_after = _utcnow() - timedelta(days=days) if platform == "steam" else None
            log.info("[backfill] 开始 %s:%s（近 %d 天）", platform, target_id, days)
            report = run_pipeline(
                platform=platform,
                target_id=target_id,
                max_count=None,
                language="schinese" if platform == "steam" else None,
                posted_after=posted_after,
                posted_before=None,
                skip_analysis=False,
            )
            fetched = report.get("fetched", 0)
            analyzed = report.get("analyzed", 0)
            _backfill_status(platform, target_id, status="done", finished_at=_utcnow().isoformat(),
                             fetched=fetched, analyzed=analyzed, error=None)
            # 回写 DB（开独立 session，不依赖请求 session）
            from src.storage.db import BilibiliQueue, CollectTask, init_db
            _, S = init_db()
            with S() as s:
                if platform == "steam":
                    t = s.execute(select(CollectTask).where(CollectTask.target_id == target_id)).scalar_one_or_none()
                    if t:
                        t.last_collected_at = _utcnow()
                elif platform == "bilibili":
                    row = s.execute(select(BilibiliQueue).where(BilibiliQueue.bv_id == target_id)).scalar_one_or_none()
                    if row:
                        row.status = "fetched"
                        row.fetched_at = _utcnow()
                        row.comment_count = fetched
                        row.danmaku_count = report.get("danmaku", 0) or 0
                        row.fail_count = 0
                        row.fail_reason = None
                s.commit()
            log.info("[backfill] 完成 %s:%s fetched=%s", platform, target_id, fetched)
        except Exception as e:  # noqa: BLE001
            log.exception("[backfill] 失败 %s:%s → %s", platform, target_id, e)
            _backfill_status(platform, target_id, status="failed", finished_at=_utcnow().isoformat(),
                             error=f"{type(e).__name__}: {e}")

    threading.Thread(target=_run, name=f"backfill-{platform}-{target_id}", daemon=True).start()


@admin_router.get("/backfill-status")
def api_backfill_status():
    """查询所有 backfill job 状态（前端轮询用；admin 鉴权已由 admin_router 依赖覆盖）"""
    return _ok(list(_backfill_jobs.values()))


class SteamTaskBody(BaseModel):
    url_or_id: str
    name: str | None = None
    language: str = "schinese"
    count: int | None = None
    backfill_days: int | None = None  # 设置则触发首次采集（近 N 天，默认语义 7）


class BiliTaskBody(BaseModel):
    url_or_id: str
    note: str | None = None
    backfill: bool = False  # True = 立即采集（跳过 7 天等待）


@admin_router.get("/tasks/lookup")
def api_lookup_task(platform: str, url_or_id: str):
    """新增/编辑弹窗的「查找」：输入 URL/ID 即时返回目标标题与日期（不落库）

    Steam → appdetails：{name, release_date}；B站 → view 接口：{title, pubdate}。
    外部调用 1~2s，FastAPI sync 路由天然在线程池执行，不阻塞事件循环。
    """
    from src.api import service
    from src.collectors.steam import SteamCollector

    if platform == "steam":
        appid = _parse_steam_appid(url_or_id)
        info = SteamCollector().fetch_app_info(appid) or {}
        if not info.get("name"):
            raise HTTPException(404, f"未查询到 AppID {appid} 的游戏信息（接口限流或不存在）")
        return _ok({
            "name": info["name"],
            "release_date": service._parse_release_date(info.get("release_date")),
        })
    if platform == "bilibili":
        from src.queue.cli import _normalize_bvid, _lookup_pubdate

        bv = _normalize_bvid(url_or_id)
        if not bv.startswith("BV"):
            raise HTTPException(422, f"无效 BV 号：{url_or_id}")
        pubdate, title = _lookup_pubdate(bv)
        if not title:
            raise HTTPException(404, f"未查询到 {bv} 的视频信息（接口限流或不存在）")
        return _ok({"title": title, "pubdate": pubdate.isoformat() if pubdate else None})
    raise HTTPException(422, "platform 仅支持 steam/bilibili")


@admin_router.get("/tasks")
def api_list_tasks(platform: str | None = None, s: Session = Depends(get_session)):
    out: dict = {}
    if platform in (None, "steam"):
        out["steam"] = [_steam_task_view(t) for t in CollectTaskRepository(s).list_all(platform="steam")]
    if platform in (None, "bilibili"):
        rows = list(s.execute(select(BilibiliQueue).order_by(BilibiliQueue.id.desc())).scalars())
        out["bilibili"] = [_bili_task_view(q) for q in rows]
    return _ok(out)


@admin_router.post("/tasks/steam", status_code=201)
async def api_create_steam_task(body: SteamTaskBody, s: Session = Depends(get_session)):
    appid = _parse_steam_appid(body.url_or_id)
    # Steam appdetails 回填名称：在线程池跑（不阻塞事件循环，其他请求可并发）
    name = body.name
    if not name:
        name = await run_in_threadpool(_fetch_steam_game_name, appid)
    repo = CollectTaskRepository(s)
    try:
        task = repo.create(
            "steam", appid,
            name=name,
            language=body.language,
            count=body.count,
            source_url=_steam_url(appid),
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    if body.backfill_days:
        _spawn_backfill("steam", appid, days=body.backfill_days)
    return _ok({**_steam_task_view(task), "backfill_started": bool(body.backfill_days)})


@admin_router.post("/tasks/bilibili", status_code=201)
async def api_create_bili_task(body: BiliTaskBody, s: Session = Depends(get_session)):
    from src.queue.cli import _normalize_bvid

    bv = _normalize_bvid(body.url_or_id)
    if not bv.startswith("BV"):
        raise HTTPException(422, f"无效 BV 号：{body.url_or_id}")
    if s.execute(select(BilibiliQueue).where(BilibiliQueue.bv_id == bv)).scalar_one_or_none():
        raise HTTPException(409, f"任务已存在：{bv}")

    # B 站 view 接口识别 pubdate+title：线程池跑（≤15s，不阻塞事件循环）
    pubdate, title = await run_in_threadpool(_bili_lookup, bv)
    row = BilibiliQueue(
        bv_id=bv,
        title=title,
        pubdate=pubdate,
        due_date=(pubdate + timedelta(days=7)) if pubdate else None,
        status="scheduled" if pubdate else "pending",
        added_by="web-admin",
        note=body.note,
    )
    s.add(row)
    s.commit()
    if body.backfill:
        _spawn_backfill("bilibili", bv)
    return _ok({**_bili_task_view(row), "backfill_started": body.backfill})


@admin_router.patch("/tasks/steam/{task_id}")
def api_update_steam_task(task_id: int, body: dict, s: Session = Depends(get_session)):
    repo = CollectTaskRepository(s)
    task = repo.get(task_id)
    if task is None:
        raise HTTPException(404, f"任务不存在：{task_id}")

    if "enabled" in body:
        repo.set_enabled(task_id, bool(body["enabled"]))
    update_kwargs = {}
    if "name" in body:
        update_kwargs["name"] = body["name"]
    if "language" in body:
        update_kwargs["language"] = body["language"]
    if "count" in body:
        if body["count"] is None:
            update_kwargs["clear_count"] = True
        else:
            update_kwargs["count"] = int(body["count"])
    if update_kwargs:
        repo.update(task_id, **update_kwargs)
    return _ok(_steam_task_view(repo.get(task_id)))


@admin_router.patch("/tasks/bilibili/{row_id}")
async def api_update_bili_task(row_id: int, body: dict, s: Session = Depends(get_session)):
    row = s.get(BilibiliQueue, row_id)
    if row is None:
        raise HTTPException(404, f"任务不存在：{row_id}")

    action = body.get("action")
    if action == "pause":
        if row.status in ("fetched",):
            raise HTTPException(409, "已采集条目无需暂停")
        row.status = "paused"
    elif action == "resume":
        # 守卫：仅 paused 可恢复；防止直接对 fetched/failed/fetching 调 resume 触发意外重采
        # （fetched 重采应走显式 revisit 路径，不在此端点）
        if row.status != "paused":
            raise HTTPException(409, f"仅 paused 状态可恢复（当前 {row.status}）")
        row.status = "scheduled" if row.pubdate else "pending"
    elif action == "reidentify":
        # B 站 view 接口：线程池跑，不阻塞事件循环
        pubdate, title = await run_in_threadpool(_bili_lookup, row.bv_id)
        if pubdate:
            row.pubdate, row.title = pubdate, title or row.title
            row.due_date = pubdate + timedelta(days=7)
            if row.status == "pending":
                row.status = "scheduled"
        else:
            raise HTTPException(502, "识别失败（B 站接口不可用或 BV 无效），稍后重试")
    elif "note" in body:
        row.note = body["note"]
    else:
        raise HTTPException(422, "action 仅支持 pause/resume/reidentify，或直接传 note")

    s.commit()
    return _ok(_bili_task_view(row))


@admin_router.delete("/tasks/steam/{task_id}")
def api_delete_steam_task(task_id: int, s: Session = Depends(get_session)):
    if not CollectTaskRepository(s).delete(task_id):
        raise HTTPException(404, f"任务不存在：{task_id}")
    return _ok({"deleted": task_id})


@admin_router.delete("/tasks/bilibili/{row_id}")
def api_delete_bili_task(row_id: int, s: Session = Depends(get_session)):
    row = s.get(BilibiliQueue, row_id)
    if row is None:
        raise HTTPException(404, f"任务不存在：{row_id}")
    if row.status == "fetched":
        raise HTTPException(409, "已采集条目不允许删除（数据已入库）；可改为暂停")
    s.delete(row)
    s.commit()
    return _ok({"deleted": row_id})
