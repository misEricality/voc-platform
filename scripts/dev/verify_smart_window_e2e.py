"""端到端验证 smart_window 集成（不联网、不污染主库）

验证 daily_incremental_collect.py 的 main 流程能正确：
1. 加载 targets.yaml
2. 算 now_utc
3. 对每个 target 调 smart_window 拿窗口
4. 调 run_pipeline（mock 掉，只看参数传递）

使用临时 DB + mock run_pipeline，不连 Steam API。
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("verify.smart_window.e2e")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def main():
    import tempfile

    # 临时测试 DB
    fd, raw_db = tempfile.mkstemp(suffix=".db", prefix="voc_verify_")
    os.close(fd)
    db_path = Path(raw_db)
    db_path.unlink()  # 让 init_db 重新建

    # 用临时 targets.yaml
    import yaml
    fd2, raw_yaml = tempfile.mkstemp(suffix=".yaml", prefix="voc_targets_")
    os.close(fd2)
    cfg_path = Path(raw_yaml)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump({
            "version": 1,
            "targets": [
                {"platform": "steam", "id": "999", "name": "Test1",
                 "language": "schinese", "count": 3, "enabled": True},
            ],
        }, f, allow_unicode=True)

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["LOG_LEVEL"] = "INFO"

    # mock run_pipeline 看 posted_after / posted_before 传递是否正确
    received = {}

    def fake_run_pipeline(*, platform, target_id, max_count, language,
                          posted_after, posted_before, skip_analysis):
        received["platform"] = platform
        received["target_id"] = target_id
        received["posted_after"] = posted_after
        received["posted_before"] = posted_before
        return {"fetched": 0, "analyzed": 0, "embedded": 0}

    from scripts.ops import daily_incremental_collect as mod
    mod.run_pipeline = fake_run_pipeline

    # 把 main() 的关键步骤手工复刻（不走 GH release download/upload）
    from src.storage.db import init_db, _utcnow
    init_db(f"sqlite:///{db_path}")
    targets = mod.load_targets(cfg_path)
    now_utc = _utcnow()

    log.info(f"=== now_utc = {now_utc}（北京时间 = {now_utc + mod.BJT_OFFSET}）")
    log.info(f"=== 加载 {len(targets)} 个目标")

    result = mod.run_one_target(targets[0], now_utc=now_utc)

    log.info(f"=== run_one_target 返回: {result}")
    log.info(f"=== run_pipeline 收到的参数:")
    for k, v in received.items():
        log.info(f"    {k} = {v}")

    # 校验：posted_after / posted_before 必须非 None（auto 模式下窗口生效）
    assert received["posted_before"] is not None, "posted_before 应该非 None（不采当天）"
    assert received["posted_after"] is not None, "posted_after 应该非 None（空 DB 起步）"
    # 校验：posted_before < now_utc（不采今天）
    assert received["posted_before"] < now_utc, \
        f"posted_before={received['posted_before']} 应该 < now_utc={now_utc}"
    # 校验：posted_after < posted_before
    assert received["posted_after"] < received["posted_before"], \
        f"posted_after={received['posted_after']} 应该 < posted_before={received['posted_before']}"

    log.info("[OK] 端到端验证通过")

    # 清理（先 dispose engine 再删 DB；Windows 上 SQLAlchemy 句柄残留是已知问题，不影响验证结果）
    from src.storage.db import init_db
    engine, _ = init_db()
    engine.dispose()
    cfg_path.unlink(missing_ok=True)
    try:
        db_path.unlink()
    except PermissionError:
        pass  # Windows 上 SQLAlchemy 可能还有句柄残留，tempdir 由系统清理

    return 0  # 显式返回 0，避免 cleanup 失败掩盖验证结果


if __name__ == "__main__":
    sys.exit(main())