"""冒烟测试 - 验证项目骨架可正常工作"""
import sys
import tempfile
import os
from datetime import datetime
from pathlib import Path

# 让脚本能找到 src 模块（项目根目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors.base import RawComment
from src.storage.db import init_db, CommentRepository


def main():
    print("[1] 模块导入 OK")

    # 数据模型测试
    rc = RawComment(
        platform="steam",
        source_id="test123",
        content="测试评论",
        rating=1,
        language="schinese",
    )
    rc.extra = {"appid": "730"}
    assert rc.platform == "steam"
    print("[2] RawComment 创建 OK")

    # 数据库测试
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    engine, Session = init_db(f"sqlite:///{tmp.name}")
    session = Session()
    repo = CommentRepository(session)
    repo.upsert(rc)
    repo.commit()
    assert repo.count() == 1
    print("[3] SQLite 存储 OK")

    # 测试分析更新
    c = repo.find_unanalyzed()[0]
    repo.update_analysis(
        c.id,
        sentiment="positive",
        sentiment_score=0.85,
        sentiment_confidence=0.92,
        topic="玩法",
        sub_topics=["有趣"],
    )
    repo.commit()
    analyzed = repo.all_analyzed()
    assert len(analyzed) == 1
    assert analyzed[0].sentiment == "positive"
    print("[4] 分析结果更新 OK")

    session.close()
    # Windows 上数据库连接可能仍持有文件句柄，跳过清理
    try:
        os.unlink(tmp.name)
    except (PermissionError, OSError):
        pass  # 临时文件会在系统重启时清理

    print()
    print("========== 所有基础测试通过 ==========")
    print("项目骨架已就绪，可以开始接真实数据了！")


if __name__ == "__main__":
    main()