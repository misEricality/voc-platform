"""P6 自动化采集编排测试

锁住的回归（详见 docs/architecture/AUTOMATION_PIPELINE.md §4 验收 + §8.1 决策 D4）：
1. 空库起步 → 采集后 DB 写入正确
2. 有库起步 → 只新增（不覆盖已有 likes_refreshed_at）
3. 时间窗计算正确（max(posted_at) - 1 天）
4. 单 target 失败不阻塞其他 target
5. --push-db（变体 ①b 推 DB 到 VPS）默认关、在采集链末尾、失败不改变退出码
   （2026-09-11 接入）
（plan/P6_AUTOMATION_PIPELINE.md 已于 2026-08-22 合并删除）

每个用例用 data/test_*.db（已被 .gitignore 排除 *.db），绝不碰 data/voc.db；
参照 scripts/dev/e2e_lifecycle.py 的「独立测试 DB」模式。
"""
from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ---------- fixtures ----------

@pytest.fixture
def test_db_path():
    """每个用例分配独立测试 DB（data/test_<uuid>.db，不污染 data/voc.db）。"""
    db = ROOT / "data" / f"voc_test_{uuid.uuid4().hex[:8]}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    return db


@pytest.fixture(autouse=True)
def _isolated_env(test_db_path, monkeypatch):
    """设置 DATABASE_URL 指向独立测试 DB；抑制真实 embedder/analyzer 加载。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setattr("src.pipeline.get_embedder", lambda: None)
    yield
    # 清理：测试结束删 DB
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except OSError:
            pass


def _write_targets_yaml(targets: list[dict]) -> Path:
    """写一个临时的 targets.yaml（用 tempfile，避免依赖 sandbox 写权限）。"""
    import tempfile

    fd, raw_path = tempfile.mkstemp(suffix=".yaml", prefix="voc_targets_")
    os.close(fd)
    p = Path(raw_path)
    try:
        import yaml
        with p.open("w", encoding="utf-8") as f:
            yaml.safe_dump({"version": 1, "targets": targets}, f, allow_unicode=True)
        return p
    except Exception:
        p.unlink(missing_ok=True)
        raise


# ---------- fake 数据层 ----------

def _make_fake_collector(comments: list):
    """替代 SteamCollector：每次 collect 都返回同一份 raws"""
    class _FakeCollector:
        def fetch_app_info(self, target_id):
            return {"name": f"Game {target_id}", "type": "game"}

        def collect(self, target_id, max_count=50, language="schinese",
                    posted_after=None, posted_before=None):
            return list(comments)

    return _FakeCollector


def _fake_analyzer_factory():
    """返回固定结果的分析器"""
    from src.analyzers.base import AnalysisResult

    class _FakeAnalyzer:
        name = "fake"

        def analyze(self, text, *, context=None):
            return self.analyze_batch([text])[0]

        def analyze_batch(self, texts, **kwargs):
            return [
                AnalysisResult(
                    sentiment="positive",
                    sentiment_score=0.5,
                    sentiment_confidence=0.9,
                    topic="玩法与内容",
                    opinions=[],
                )
                for _ in texts
            ]

    return _FakeAnalyzer()


def _make_raw_comment(source_id: str, content: str, appid: str, posted_at):
    from src.collectors.base import RawComment
    return RawComment(
        platform="steam",
        source_id=source_id,
        content=content,
        author_id=f"u-{source_id}",
        rating=1,
        language="schinese",
        posted_at=posted_at,
        extra={"appid": appid},
    )


# ---------- 用例 1：空库起步 → 采集后 DB 写入正确 ----------

def test_load_targets_and_first_run_writes_to_empty_db(monkeypatch):
    """空库起步 → 加载 targets.yaml → 跑 run_one_target → DB 中应写入目标评论"""
    from sqlalchemy import select
    from src.storage.db import init_db, Comment

    targets_cfg = _write_targets_yaml([{
        "platform": "steam", "id": "999", "name": "Test Game",
        "language": "schinese", "count": 3, "enabled": True,
    }])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    raws = [_make_raw_comment(f"r{i}", f"content-{i}", "999", now) for i in range(3)]

    fake_collector = _make_fake_collector(raws)
    from src.pipeline import COLLECTORS
    monkeypatch.setitem(COLLECTORS, "steam", fake_collector)
    monkeypatch.setattr("src.pipeline.get_analyzer", lambda provider=None: _fake_analyzer_factory())

    from scripts.ops.daily_incremental_collect import load_targets, run_one_target
    targets = load_targets(targets_cfg)
    assert len(targets) == 1
    assert targets[0]["id"] == "999"

    result = run_one_target(targets[0], now_utc=now)
    assert result["ok"] is True
    assert result["fetched"] == 3
    assert result["analyzed"] == 3

    engine, SessionLocal = init_db()
    with SessionLocal() as s:
        rows = list(s.execute(select(Comment)).scalars())
        assert len(rows) == 3
        for r in rows:
            assert r.target_id == "steam:999"
            assert r.platform == "steam"


# ---------- 用例 2a：时间窗计算（max - 1 天） ----------

def test_calc_posted_after_uses_max_minus_one_day(monkeypatch):
    """时间窗起点 = 该目标在 DB 中 max(posted_at) 减 1 天"""
    from src.storage.db import init_db, CommentRepository

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old_ts = now - timedelta(days=5)
    recent_ts = now - timedelta(days=1)
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        repo = CommentRepository(s)
        repo.bulk_upsert([
            _make_raw_comment("r-old", "old content", "999", old_ts),
            _make_raw_comment("r-new", "new content", "999", recent_ts),
        ])

    from scripts.ops.daily_incremental_collect import calc_posted_after
    posted_after = calc_posted_after("steam:999", lookback_days=1)

    expected = recent_ts - timedelta(days=1)
    assert posted_after == expected, f"应为 {expected}，实际 {posted_after}"


# ---------- 用例 2b：目标无数据 → 返回 None ----------

def test_calc_posted_after_returns_none_when_no_data(monkeypatch):
    """目标在 DB 中无数据 → 返回 None（全量起步）"""
    from src.storage.db import init_db

    init_db()  # 建空表
    from scripts.ops.daily_incremental_collect import calc_posted_after
    posted_after = calc_posted_after("steam:nonexistent")
    assert posted_after is None


# ---------- 用例 3：有库起步 → 不覆盖已有 likes_refreshed_at ----------

def test_incremental_run_preserves_existing_data(monkeypatch):
    """二次采集不应擦掉已有评论的 likes / likes_refreshed_at（冷启动 NULL 语义）"""
    from sqlalchemy import select
    from src.storage.db import init_db, Comment, CommentRepository

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        repo = CommentRepository(s)
        repo.bulk_upsert([_make_raw_comment("r1", "existing content", "999", now - timedelta(days=3))])
        # 模拟已回采（写入 likes + 时间戳）
        existing = list(s.execute(select(Comment)).scalars())[0]
        existing.likes = 42
        existing.likes_refreshed_at = now - timedelta(days=1)
        s.commit()

    # 再跑一次 run_one_target：raws 是空（增量语义：模拟没有新评论）
    fake_collector = _make_fake_collector([])
    from src.pipeline import COLLECTORS
    monkeypatch.setitem(COLLECTORS, "steam", fake_collector)
    monkeypatch.setattr("src.pipeline.get_analyzer", lambda provider=None: _fake_analyzer_factory())

    from scripts.ops.daily_incremental_collect import load_targets, run_one_target
    targets_cfg = _write_targets_yaml([{
        "platform": "steam", "id": "999", "name": "Test",
        "language": "schinese", "count": 30, "enabled": True,
    }])
    targets = load_targets(targets_cfg)
    result = run_one_target(targets[0], now_utc=now)

    # 验证：likes=42 likes_refreshed_at 没被擦掉
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        rows = list(s.execute(select(Comment)).scalars())
        assert len(rows) == 1
        assert rows[0].likes == 42, f"已有 likes 应保留，实际为 {rows[0].likes}"
        assert rows[0].likes_refreshed_at == now - timedelta(days=1), "已有 likes_refreshed_at 应保留"


# ---------- 用例 4：单 target 失败不阻塞其他 target ----------

def test_single_target_failure_does_not_block_others(monkeypatch):
    """run_one_target 内部异常被捕获 → 后续 target 仍应正常执行"""
    from scripts.ops.daily_incremental_collect import load_targets, run_one_target

    targets_cfg = _write_targets_yaml([
        {"platform": "steam", "id": "fail-id", "name": "Bad", "language": "schinese", "count": 30, "enabled": True},
        {"platform": "steam", "id": "ok-id", "name": "Good", "language": "schinese", "count": 30, "enabled": True},
    ])

    call_count = {"n": 0}

    def fake_run_pipeline(**kwargs):
        call_count["n"] += 1
        if kwargs.get("target_id") == "fail-id":
            raise RuntimeError("simulated network error")
        return {"fetched": 1, "analyzed": 1, "embedded": 0}

    monkeypatch.setattr("scripts.ops.daily_incremental_collect.run_pipeline", fake_run_pipeline)

    targets = load_targets(targets_cfg)
    results = [run_one_target(t, now_utc=datetime.now(timezone.utc).replace(tzinfo=None)) for t in targets]

    assert call_count["n"] == 2, "应尝试两个 target"
    assert results[0]["ok"] is False
    assert "simulated network error" in results[0]["error"]
    assert results[1]["ok"] is True
    assert results[1]["fetched"] == 1


# ---------- 用例 5（额外）：gh_release_exists 在 gh 不可用时不应崩 ----------

def test_gh_release_exists_handles_missing_gh_cli(monkeypatch):
    """本地无 gh CLI 时不应抛异常（仅返回 False）"""
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=["gh"], returncode=127, stderr="gh: not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    from scripts.ops.daily_incremental_collect import gh_release_exists
    assert gh_release_exists("nonexistent") is False


# ---------- 用例 6：smart_window 正常场景（max_ts 推进到昨天 workflow 跑完时间） ----------

def test_smart_window_normal_yesterday_max():
    """DB max_ts ≈ 北京昨天 8:00（昨天 workflow 成功）→ posted_after=max_ts-1d, posted_before=北京今天 0:00

    行为矩阵第 1 行：
    ┌──────────────────────────────┬─────────────────────────────┐
    │ DB max_ts ≈ 北京昨天 8:00   │ posted_after = 北京前天 8:00 │
    │ （正常）                    │ posted_before = 北京今天 0:00│
    └──────────────────────────────┴─────────────────────────────┘
    """
    from src.storage.db import init_db, CommentRepository

    now_utc = datetime(2026, 8, 29, 0, 0, 0)   # 北京 8/29 8:00
    max_ts = datetime(2026, 8, 28, 0, 0, 0)     # 北京 8/28 8:00（昨天 release 的 max）

    _, SessionLocal = init_db()
    with SessionLocal() as s:
        repo = CommentRepository(s)
        repo.bulk_upsert([_make_raw_comment("r1", "content", "999", max_ts)])

    from scripts.ops.daily_incremental_collect import smart_window
    posted_after, posted_before = smart_window("steam:999", now_utc)

    # posted_before: 北京 8/29 0:00 → UTC 8/28 16:00
    assert posted_before == datetime(2026, 8, 28, 16, 0, 0), f"posted_before={posted_before}"
    # posted_after: max_ts - 1d = UTC 8/27 0:00（北京 8/27 8:00），floor = UTC 8/26 16:00（不生效）
    assert posted_after == datetime(2026, 8, 27, 0, 0, 0), f"posted_after={posted_after}"


# ---------- 用例 7：smart_window 补救场景（昨天 workflow 失败，floor 生效） ----------

def test_smart_window_recovery_floor_engages():
    """DB max_ts ≈ 北京前天 8:00（昨天 workflow 失败）→ posted_after=floor 北京前天 0:00

    行为矩阵第 2 行：
    ┌──────────────────────────────┬─────────────────────────────┐
    │ DB max_ts ≈ 北京前天 8:00   │ posted_after = 北京前天 0:00 │
    │ （昨天失败，floor 生效）     │ posted_before = 北京今天 0:00│
    └──────────────────────────────┴─────────────────────────────┘
    """
    from src.storage.db import init_db, CommentRepository

    now_utc = datetime(2026, 8, 29, 0, 0, 0)
    max_ts = datetime(2026, 8, 27, 0, 0, 0)    # 北京 8/27 8:00（昨天 workflow 没成功，max 还停在这）

    _, SessionLocal = init_db()
    with SessionLocal() as s:
        repo = CommentRepository(s)
        repo.bulk_upsert([_make_raw_comment("r1", "content", "999", max_ts)])

    from scripts.ops.daily_incremental_collect import smart_window
    posted_after, posted_before = smart_window("steam:999", now_utc)

    # posted_before: 北京 8/29 0:00 UTC 表示 = UTC 8/28 16:00
    assert posted_before == datetime(2026, 8, 28, 16, 0, 0), f"posted_before={posted_before}"
    # posted_after: target_posted_after = UTC 8/26 0:00 < floor = UTC 8/26 16:00 → floor 生效
    assert posted_after == datetime(2026, 8, 26, 16, 0, 0), f"posted_after={posted_after}"


# ---------- 用例 8：smart_window 空 DB ----------

def test_smart_window_empty_db():
    """DB 无数据 → posted_after=floor（空 DB 起步采前天 + 昨天）

    行为矩阵第 4 行（特例）：
    ┌──────────────────────────────┬─────────────────────────────┐
    │ DB 无数据                   │ posted_after = floor         │
    │                              │ posted_before = 北京今天 0:00│
    └──────────────────────────────┴─────────────────────────────┘
    """
    from src.storage.db import init_db

    now_utc = datetime(2026, 8, 29, 0, 0, 0)
    init_db()  # 建空表

    from scripts.ops.daily_incremental_collect import smart_window
    posted_after, posted_before = smart_window("steam:nonexistent", now_utc)

    assert posted_before == datetime(2026, 8, 28, 16, 0, 0)
    assert posted_after == datetime(2026, 8, 26, 16, 0, 0)


# ---------- 用例 9b：lookback_days=7（本地直采多日重叠采样，2026-09-03） ----------

def test_smart_window_seven_day_lookback():
    """lookback_days=7：floor = 北京 7 天前 0:00 → 窗口覆盖近 7 个北京日历日

    背景：Steam filter=recent 游标流是非确定性采样（单次漏 5-20%），
    7 天重叠回看 + upsert 幂等 + analyzed-skip 使覆盖率随多遍采样收敛。
    本地直采计划任务传 --lookback-days 7。
    """
    from scripts.ops.daily_incremental_collect import smart_window

    now_utc = datetime(2026, 9, 3, 18, 0, 0)   # 北京 9/4 02:00
    posted_after, posted_before = smart_window(
        "steam:nonexistent", now_utc, lookback_days=7
    )
    # posted_before: 北京 9/4 0:00 → UTC 9/3 16:00（当天 9/4 严格不采）
    assert posted_before == datetime(2026, 9, 3, 16, 0, 0)
    # floor: 北京 8/28 0:00 → UTC 8/27 16:00（覆盖 8/28 ~ 9/3 共 7 个日历日）
    assert posted_after == datetime(2026, 8, 27, 16, 0, 0)


def test_smart_window_default_lookback_is_two():
    """默认不传 lookback_days → 保持 2 天行为（昨天+前天，向后兼容）"""
    from scripts.ops.daily_incremental_collect import smart_window

    now_utc = datetime(2026, 9, 3, 18, 0, 0)
    posted_after, posted_before = smart_window("steam:nonexistent", now_utc)
    # posted_before: 北京 9/4 0:00 → UTC 9/3 16:00
    assert posted_before == datetime(2026, 9, 3, 16, 0, 0)
    # floor: 北京 9/2 0:00 → UTC 9/1 16:00（覆盖 9/2 + 9/3 前两天，与原行为一致）
    assert posted_after == datetime(2026, 9, 1, 16, 0, 0)


# ---------- 用例 9：smart_window BJT 跨 UTC 日界 ----------

def test_smart_window_bjt_midnight_boundary():
    """now_utc 在 UTC 日界附近（北京刚跨入新一天）→ 窗口仍按北京日历日正确切分

    边界场景：now_utc = UTC 8/28 16:01 = 北京 8/29 0:01
    → 北京日历日 = 8/29（不是 8/28）
    → posted_before 应 = 北京 8/29 0:00 UTC 表示 = UTC 8/28 16:00
    """
    from src.storage.db import init_db, CommentRepository

    now_utc = datetime(2026, 8, 28, 16, 1, 0)  # 北京 8/29 0:01（北京时间刚跨日）
    max_ts = datetime(2026, 8, 28, 0, 0, 0)     # 北京 8/28 8:00

    _, SessionLocal = init_db()
    with SessionLocal() as s:
        repo = CommentRepository(s)
        repo.bulk_upsert([_make_raw_comment("r1", "content", "999", max_ts)])

    from scripts.ops.daily_incremental_collect import smart_window
    posted_after, posted_before = smart_window("steam:999", now_utc)

    # posted_before: 北京 8/29 0:00 UTC 表示（注意：now_utc UTC 日是 8/28，但北京已 8/29）
    assert posted_before == datetime(2026, 8, 28, 16, 0, 0), f"posted_before={posted_before}"
    # posted_after: max_ts - 1d = UTC 8/27 0:00（北京 8/27 8:00），floor = UTC 8/26 16:00
    assert posted_after == datetime(2026, 8, 27, 0, 0, 0), f"posted_after={posted_after}"


# ---------- 用例 10：--push-db 开关（变体 ①b 数据通道，2026-09-11） ----------
#
# 契约（见 docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md §0.5 + §11）：
#   1. 默认关：不传 --push-db 就不推（GH Actions / 手动调试不受影响）
#   2. 传了才推，且只推一次，参数是 --db-path 指向的那个库
#   3. 推送必须发生在采集链路末尾（否则会推出半成品 DB）
#   4. 推送失败只告警，绝不改变采集退出码（本机是唯一数据源，VPS 只是展示端）

def _run_main(monkeypatch, test_db_path, extra_argv, push_result=True):
    """跑 main()：屏蔽真实网络/采集/B站队列，只观察 push_db_to_vps 是否被调用。"""
    import sys

    import scripts.ops.daily_incremental_collect as mod

    calls = []

    def fake_push(db_path, **kwargs):
        calls.append(Path(db_path))
        return push_result

    monkeypatch.setattr(mod, "push_db_to_vps", fake_push)
    monkeypatch.setattr(mod, "load_targets_any", lambda *a, **k: [])
    monkeypatch.setattr(mod, "run_bilibili_queue", lambda **k: {
        "target": "bilibili:run-due (due=0)", "ok": True,
        "fetched": 0, "analyzed": 0, "embedded": 0, "error": None,
    })
    monkeypatch.setattr(sys, "argv", [
        "daily_incremental_collect.py", "--no-download", "--no-upload",
        "--db-path", str(test_db_path),
        "--targets-config", str(ROOT / "config" / "monitoring" / "targets.yaml"),
    ] + extra_argv)
    mod.main()
    return calls


def test_push_db_disabled_by_default(monkeypatch, test_db_path):
    """不传 --push-db → 不触发 VPS 推送（默认关，GH Actions 与手动调试不受影响）"""
    assert _run_main(monkeypatch, test_db_path, []) == []


def test_push_db_flag_calls_pusher_once_with_db_path(monkeypatch, test_db_path):
    """传 --push-db → 恰好推一次，且推的是 --db-path 指定的库"""
    calls = _run_main(monkeypatch, test_db_path, ["--push-db"])
    assert calls == [Path(str(test_db_path))], calls


def test_push_db_runs_after_collect_chain(monkeypatch, test_db_path):
    """顺序回归：push 必须在 B站 run-due + 摘要之后，否则推出的是半成品 DB"""
    import sys

    import scripts.ops.daily_incremental_collect as mod

    order = []
    monkeypatch.setattr(mod, "load_targets_any", lambda *a, **k: [])
    monkeypatch.setattr(mod, "run_bilibili_queue", lambda **k: (
        order.append("collect") or
        {"target": "bilibili", "ok": True, "fetched": 0, "analyzed": 0, "embedded": 0, "error": None}
    ))
    monkeypatch.setattr(mod, "emit_step_summary", lambda results: order.append("summary"))
    monkeypatch.setattr(mod, "push_db_to_vps", lambda db, **k: order.append("push") or True)
    monkeypatch.setattr(sys, "argv", [
        "daily_incremental_collect.py", "--no-download", "--no-upload",
        "--db-path", str(test_db_path), "--push-db",
    ])
    mod.main()
    assert order == ["collect", "summary", "push"], order


def test_push_db_failure_does_not_change_exit_code(monkeypatch, test_db_path):
    """推送返回 False → main() 正常返回，不 sys.exit(1)（失败不阻塞采集）"""
    calls = _run_main(monkeypatch, test_db_path, ["--push-db"], push_result=False)
    assert len(calls) == 1


def test_push_db_to_vps_returns_false_when_script_missing(tmp_path):
    """推送脚本不存在 → 仅告警并返回 False（不是异常）"""
    from scripts.ops.daily_incremental_collect import push_db_to_vps
    assert push_db_to_vps(tmp_path / "voc.db", script=tmp_path / "missing.ps1") is False


def test_push_db_to_vps_returns_false_on_nonzero_rc(monkeypatch, tmp_path):
    """推送脚本非零退出（scp/远端校验失败）→ 返回 False，不抛异常"""
    import scripts.ops.daily_incremental_collect as mod

    script = tmp_path / "push.ps1"
    script.write_text("exit 1", encoding="utf-8")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        args=["powershell"], returncode=1, stdout="", stderr="scp failed"))
    assert mod.push_db_to_vps(tmp_path / "voc.db", script=script) is False


def test_push_db_to_vps_returns_false_when_powershell_missing(monkeypatch, tmp_path):
    """无 powershell（非 Windows / PATH 缺失）→ 返回 False，不抛异常"""
    import scripts.ops.daily_incremental_collect as mod

    script = tmp_path / "push.ps1"
    script.write_text("exit 0", encoding="utf-8")

    def boom(*a, **k):
        raise FileNotFoundError("powershell not found")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert mod.push_db_to_vps(tmp_path / "voc.db", script=script) is False


def test_push_db_to_vps_uses_explicit_utf8_encoding(monkeypatch, tmp_path):
    """回归：子进程输出必须显式 utf-8 解码（2026-09-09 Windows gbk 解码教训）"""
    import scripts.ops.daily_incremental_collect as mod

    script = tmp_path / "push.ps1"
    script.write_text("exit 0", encoding="utf-8")
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="DONE", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.push_db_to_vps(tmp_path / "voc.db", script=script) is True
    assert seen["kwargs"].get("encoding") == "utf-8"
    assert str(script) in seen["cmd"]
    assert str(tmp_path / "voc.db") in seen["cmd"]


# ---------- main（直接跑时） ----------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])