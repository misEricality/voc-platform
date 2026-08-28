"""Streamlit 仪表盘 - 灵听 · Lynx

启动：streamlit run app.py

视图：
- 单目标看板（评论概览 + 情感/主题图表 + 典型样本）
- 多目标对比（横向口碑、主题 × 游戏热力图）
- 📋 明细核查（按 DB 表切换 + 多维筛选 + 分页 + CSV 导出，与 DB Browser 互补）
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from wordcloud import WordCloud
import matplotlib.pyplot as plt
import pandas as pd
from sqlalchemy import exists, func, or_, select

from src.storage.db import (
    BilibiliQueue,
    Comment,
    CommentOpinion,
    CommentRepository,
    Danmaku,
    init_db,
)
from src.visualizer.charts import (
    extract_keywords,
    build_summary_dataframe,
    sentiment_distribution,
    topic_distribution,
    sentiment_stacked,
    topic_game_heatmap,
    position_scatter,
)


LOGO_PATH = Path(__file__).resolve().parent / "product" / "logo" / "lynx_logo_v4a_clean.png"


def _find_chinese_font() -> str | None:
    """跨平台探测可用的中文字体路径（词云渲染中文必需，否则显示为方框）"""
    candidates = [
        # Windows
        "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
        "C:/Windows/Fonts/msyhbd.ttc",     # 微软雅黑粗体
        "C:/Windows/Fonts/simhei.ttf",     # 黑体
        "C:/Windows/Fonts/simsun.ttc",     # 宋体
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


# ============================================================
# 明细核查视图 - 共用工具
# ============================================================

# 各表可选行（人工核对常用的主维度）
DETAIL_TABLES = {
    "comments": "📝 评论（comments）",
    "comment_opinions": "💡 观点（comment_opinions）",
    "danmaku": "🎬 弹幕（danmaku）",
    "bilibili_queue": "📋 B 站采集队列（bilibili_queue）",
}

# 长文本列（展示截断，CSV 仍保留完整）
LONG_TEXT_COLS = {"content", "quote", "title", "note", "fail_reason"}


def _paginated_query(
    session,
    base_stmt,
    page_size: int,
    page_num: int,
) -> tuple[pd.DataFrame, int]:
    """对 SQLAlchemy 语句做分页 + 计数

    Args:
        session: SQLAlchemy Session
        base_stmt: 已带 WHERE/ORDER BY 的 SELECT 语句（**不要预先 .offset/.limit**）
        page_size: 每页条数
        page_num: 页码（1-based）

    Returns:
        (df, total_rows)

    实现要点：
    - count 通过 subquery() 复用同一 WHERE，避免重写过滤条件
    - subquery() 在 SQLAlchemy 2.x 对未带 OFFSET/LIMIT 的语句稳定
    - **兼容 ORM-class-style select**（`select(Model)`）：其 row._mapping 在 SA 2.x
      是 ``{"Model": <instance>}`` 而非列名字典 → 检测到后展开 ORM 实例属性
    """
    sub = base_stmt.subquery()
    total = session.execute(select(func.count()).select_from(sub)).scalar() or 0

    if total == 0:
        return pd.DataFrame(), 0

    offset = (page_num - 1) * page_size
    paged_stmt = base_stmt.offset(offset).limit(page_size)
    rows = session.execute(paged_stmt).fetchall()
    if not rows:
        return pd.DataFrame(), total

    first_row = rows[0]
    keys = list(first_row._mapping.keys())

    # ORM-class-style select 兼容：单 key + value 是 ORM 实例 → 展开为列名字典
    if len(keys) == 1 and hasattr(first_row[0], "__table__"):
        df = pd.DataFrame([_orm_row_to_dict(r[0]) for r in rows])
    else:
        # 正常 select(*columns) / 带 .label() / 带 JOIN 显式 select：列名已在 keys
        df = pd.DataFrame([dict(r._mapping) for r in rows])
    return df, total


def _orm_row_to_dict(obj) -> dict:
    """把 ORM 实例展开为 ``{列名: 值}`` 字典

    优先用 ``__table__.columns`` 反射，避免依赖每个模型都实现 ``to_dict()``，
    同时保留原生类型（datetime 不被提前 ISO 化）。
    """
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


def _date_range_to_clause(value, col):
    """把 ``st.date_input`` 的返回值转成 SQLAlchemy ``between`` 条件

    ``date_input`` 三种返回值（Streamlit 行为，1.x 至今）：
    - ``()``                → 不限 → 返回 ``None``
    - ``(date,)``           → 单日期 → 当天 00:00:00 ~ 23:59:59.999999
    - ``(date, date)``      → 区间   → 起止当天 00:00:00 ~ 23:59:59.999999
    - 单 ``datetime.date``  → 同 (date,)（旧版兼容）

    Args:
        value: ``st.date_input`` 返回值
        col: SQLAlchemy Column（datetime 类型）

    Returns:
        ``ColumnElement[bool]`` 或 ``None``（不限时）
    """
    if isinstance(value, tuple):
        dates = value
    elif hasattr(value, "year") and not isinstance(value, tuple):
        dates = (value,)
    else:
        return None

    if not dates or any(d is None for d in dates):
        return None

    if len(dates) == 1:
        s = e = dates[0]
    else:
        s, e = dates[0], dates[-1]

    return col.between(
        datetime.combine(s, datetime.min.time()),
        datetime.combine(e, datetime.max.time()),
    )


def _distinct_values(repo, col) -> list:
    """拉取某列 distinct 值（去 None / 空串，按可比较排序）"""
    stmt = select(col).distinct()
    vals = [r for (r,) in repo.session.execute(stmt)]
    vals = [v for v in vals if v is not None and v != ""]
    try:
        return sorted(vals)
    except TypeError:
        return sorted(vals, key=str)


def _truncate_cell(val, width: int = 150) -> str:
    """长文本截断（仅展示用；CSV 仍完整）"""
    if val is None:
        return ""
    s = str(val)
    return s if len(s) <= width else s[:width] + "…"


def _render_table_with_csv(
    df: pd.DataFrame,
    total: int,
    page_size: int,
    page_num: int,
    table_name: str,
    display_cols: list[str],
    json_cols: list[str] | None = None,
) -> None:
    """统一表格渲染：分页摘要 + 表格（长文本截断）+ 当前页 CSV 导出

    json_cols: 形如 "sub_topics" 的 JSON 字符串列 → 解析为可读形式
    """
    json_cols = json_cols or []

    if total == 0:
        st.info("无匹配数据 — 请调整筛选条件或切换表。")
        return

    total_pages = max(1, (total + page_size - 1) // page_size)
    # 页码超出范围时给提示，避免显示空表让人困惑
    if page_num > total_pages:
        st.warning(
            f"当前页码 **{page_num}** 超出总页数 **{total_pages}**，请回到第 1 页。"
        )
    st.caption(
        f"共 **{total}** 条 · **{total_pages}** 页 · 当前第 **{page_num}** 页 · 每页 {page_size}"
    )

    avail = [c for c in display_cols if c in df.columns]
    show_df = df[avail].copy()

    # JSON 列：解析为可读字符串（仅展示用）
    for c in json_cols:
        if c not in show_df.columns:
            continue
        show_df[c] = show_df[c].apply(
            lambda x: (
                json.dumps(json.loads(x), ensure_ascii=False, separators=(",", ": "))
                if isinstance(x, str) and x.startswith("[")
                else (x if x else "")
            )
        )

    # 展示版：长文本截断（不修改原 df，CSV 用未截断版）
    display_df = show_df.copy()
    for c in display_df.columns:
        if c in LONG_TEXT_COLS:
            display_df[c] = display_df[c].apply(lambda v: _truncate_cell(v, 150))

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # CSV 导出：用未截断的 show_df
    csv_bytes = show_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label=f"📥 导出本页 CSV（{len(show_df)} 条 · {len(avail)} 列）",
        data=csv_bytes,
        file_name=f"{table_name}_p{page_num}.csv",
        mime="text/csv",
    )


# ============================================================
# 明细核查视图 - 4 张子表渲染
# ============================================================


def render_details(repo) -> None:
    """📋 明细核查视图 — 按表切换 + 多维筛选 + 分页 + CSV 导出

    定位：DB Browser 用于单行快速核查；本视图用于按维度批量浏览、人工
    核对采集完整性与标注质量。Streamlit 多维度分析的一部分。

    主维度（每张表不同）：
    - comments          : 平台 / 目标 / 情感 / 主题 / analyzer_version / 状态 / 时间 / 内容关键词
    - comment_opinions  : 情感 / L1 路径 / 目标（join）/ 置信度 / 观点文本关键词
    - danmaku           : 视频 / 弹幕模式 / 进度区间 / 内容关键词
    - bilibili_queue    : 状态 / due_date / 重访 / 失败次数 / BV 号 / 标题关键词
    """
    st.subheader("📋 明细核查")
    st.caption(
        "按表切换 + 多维筛选 + 分页浏览 + CSV 导出 ｜ 与 DB Browser 互为补充"
    )

    # ====== 顶部控件：表切换 + 分页 ======
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    with c1:
        selected_table = st.selectbox(
            "切换表",
            list(DETAIL_TABLES.keys()),
            format_func=lambda k: DETAIL_TABLES[k],
            key="details_table",
        )
    with c2:
        page_size = st.selectbox(
            "每页", [50, 100, 200, 500, 1000], index=1, key="details_ps",
        )
    with c3:
        page_num = st.number_input(
            "页码", min_value=1, value=1, step=1, key="details_pn",
        )
    with c4:
        st.write("")
        if st.button("↩️ 第 1 页", use_container_width=True):
            st.session_state["details_pn"] = 1
            st.rerun()

    # ====== 切换表时自动把页码重置回 1（避免越界） ======
    prev = st.session_state.get("details_prev_table")
    if prev is None:
        st.session_state["details_prev_table"] = selected_table
    elif prev != selected_table:
        st.session_state["details_prev_table"] = selected_table
        st.session_state["details_pn"] = 1
        st.rerun()

    # ====== 路由到子表渲染 ======
    if selected_table == "comments":
        _render_details_comments(repo, page_size, page_num)
    elif selected_table == "comment_opinions":
        _render_details_opinions(repo, page_size, page_num)
    elif selected_table == "danmaku":
        _render_details_danmaku(repo, page_size, page_num)
    elif selected_table == "bilibili_queue":
        _render_details_bilibili_queue(repo, page_size, page_num)


def _render_details_comments(repo, page_size: int, page_num: int) -> None:
    """comments 表 — 主维度 = 平台 / 目标 / 情感 / 主题 / analyzer_version / 状态 / 内容关键词"""
    session = repo.session

    with st.spinner("加载筛选选项..."):
        platforms = _distinct_values(repo, Comment.platform)
        target_ids = _distinct_values(repo, Comment.target_id)
        sentiments = ["positive", "neutral", "negative"]
        topics = _distinct_values(repo, Comment.topic)
        analyzer_versions = _distinct_values(repo, Comment.analyzer_version)

    with st.expander("🔧 筛选条件", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            f_platforms = st.multiselect("平台", platforms, key="d_c_platforms")
            f_sentiments = st.multiselect("情感", sentiments, key="d_c_senti")
        with c2:
            f_targets = st.multiselect("目标 (target_id)", target_ids, key="d_c_targets")
            f_topics = st.multiselect("主题 (L1)", topics, key="d_c_topics")
        with c3:
            f_versions = st.multiselect(
                "analyzer_version", analyzer_versions, key="d_c_ver"
            )
            f_analyzed = st.selectbox(
                "分析状态", ["全部", "仅已分析", "仅未分析"], key="d_c_ana",
            )
            f_has_opinions = st.checkbox("仅含观点（EXISTS 关联）", key="d_c_has_op")

        c4, c5 = st.columns([4, 1])
        with c4:
            content_kw = st.text_input(
                "内容关键词（子串匹配，LIKE %kw%）", "", key="d_c_kw"
            )
        with c5:
            st.caption("💡 内容较长 → 展示截断 150 字，CSV 完整")

        c6, c7 = st.columns(2)
        with c6:
            posted_range = st.date_input(
                "posted_at 范围（评论发布时间）",
                value=(),
                key="d_c_posted",
                help="留空 = 不限；单值 = 当天；两值 = 闭区间（按本地时区日历日）",
            )
        with c7:
            fetched_range = st.date_input(
                "fetched_at 范围（入库时间）",
                value=(),
                key="d_c_fetched",
                help="留空 = 不限；单值 = 当天；两值 = 闭区间",
            )

    stmt = select(Comment)
    if f_platforms:
        stmt = stmt.where(Comment.platform.in_(f_platforms))
    if f_targets:
        stmt = stmt.where(Comment.target_id.in_(f_targets))
    if f_sentiments:
        stmt = stmt.where(Comment.sentiment.in_(f_sentiments))
    if f_topics:
        stmt = stmt.where(Comment.topic.in_(f_topics))
    if f_versions:
        stmt = stmt.where(Comment.analyzer_version.in_(f_versions))
    if f_analyzed == "仅已分析":
        stmt = stmt.where(Comment.analyzed_at.is_not(None))
    elif f_analyzed == "仅未分析":
        stmt = stmt.where(Comment.analyzed_at.is_(None))
    if f_has_opinions:
        stmt = stmt.where(exists().where(CommentOpinion.comment_id == Comment.id))
    if content_kw:
        stmt = stmt.where(Comment.content.contains(content_kw))
    # 日期范围（共用 helper 避免 date_input 怪返回类型）
    # ⚠️ 必须 is not None 比较 — SA 2.x 禁止把 clause 当 Python bool 用（会抛
    # "Boolean value of this clause is not defined"）。不要用 `if cond:`。
    cond_posted = _date_range_to_clause(posted_range, Comment.posted_at)
    if cond_posted is not None:
        stmt = stmt.where(cond_posted)
    cond_fetched = _date_range_to_clause(fetched_range, Comment.fetched_at)
    if cond_fetched is not None:
        stmt = stmt.where(cond_fetched)

    stmt = stmt.order_by(Comment.id.desc())

    df, total = _paginated_query(session, stmt, page_size, page_num)
    _render_table_with_csv(
        df, total, page_size, page_num, "comments",
        display_cols=[
            "id", "platform", "target_id", "sentiment", "sentiment_score",
            "sentiment_confidence", "topic", "analyzer_version",
            "posted_at", "fetched_at", "analyzed_at", "content",
        ],
        json_cols=["sub_topics"],
    )


def _render_details_opinions(repo, page_size: int, page_num: int) -> None:
    """comment_opinions 表 — 主维度 = 情感 / L1 路径 / 目标（join）/ 置信度 / 观点关键词"""
    session = repo.session

    with st.spinner("加载筛选选项..."):
        sentiments = ["positive", "neutral", "negative"]
        all_paths = _distinct_values(repo, CommentOpinion.full_path)
        l1_paths = sorted({p.split("/")[0] for p in all_paths if p})
        target_ids = _distinct_values(repo, Comment.target_id)

    with st.expander("🔧 筛选条件", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            f_sentiments = st.multiselect("观点情感", sentiments, key="d_o_senti")
            f_targets = st.multiselect(
                "目标（join comments.target_id）", target_ids, key="d_o_targets"
            )
        with c2:
            f_l1 = st.multiselect("L1 主题（前缀匹配）", l1_paths, key="d_o_l1")
            f_kw = st.text_input(
                "观点文本关键词（quote 子串）", "", key="d_o_kw"
            )
        with c3:
            include_null_conf = st.checkbox(
                "包含置信度为 NULL 的观点", value=True, key="d_o_nullc"
            )
            min_conf = st.slider(
                "最小置信度（设了最小值时生效）",
                0.0, 1.0, 0.0, 0.05, key="d_o_conf",
            )

    # 注意：opinion 视图 join comments 才能取到 target_id / platform
    stmt = select(
        CommentOpinion.id.label("op_id"),
        CommentOpinion.comment_id,
        Comment.platform,
        Comment.target_id,
        CommentOpinion.full_path,
        CommentOpinion.sentiment,
        CommentOpinion.sentiment_confidence,
        CommentOpinion.quote,
        CommentOpinion.created_at,
    ).join(Comment, Comment.id == CommentOpinion.comment_id)

    if f_sentiments:
        stmt = stmt.where(CommentOpinion.sentiment.in_(f_sentiments))
    if f_targets:
        stmt = stmt.where(Comment.target_id.in_(f_targets))
    if f_l1:
        # 任意 L1 = 前缀匹配 full_path LIKE 'L1/%'
        l1_wheres = [CommentOpinion.full_path.like(f"{l1}/%") for l1 in f_l1]
        stmt = stmt.where(or_(*l1_wheres))
    if f_kw:
        stmt = stmt.where(CommentOpinion.quote.contains(f_kw))
    if min_conf > 0:
        if include_null_conf:
            stmt = stmt.where(
                or_(
                    CommentOpinion.sentiment_confidence.is_(None),
                    CommentOpinion.sentiment_confidence >= min_conf,
                )
            )
        else:
            stmt = stmt.where(CommentOpinion.sentiment_confidence >= min_conf)

    stmt = stmt.order_by(CommentOpinion.id.desc())

    df, total = _paginated_query(session, stmt, page_size, page_num)
    _render_table_with_csv(
        df, total, page_size, page_num, "comment_opinions",
        display_cols=[
            "op_id", "comment_id", "platform", "target_id", "full_path",
            "sentiment", "sentiment_confidence", "created_at", "quote",
        ],
    )


def _render_details_danmaku(repo, page_size: int, page_num: int) -> None:
    """danmaku 表 — 主维度 = 视频 / 弹幕模式 / 进度区间 / 内容关键词"""
    session = repo.session

    DANMAKU_MODE_LABELS = {
        1: "滚动", 4: "底部", 5: "顶部", 7: "高级",
    }

    with st.spinner("加载筛选选项..."):
        video_ids = _distinct_values(repo, Danmaku.video_id)

    with st.expander("🔧 筛选条件", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            f_videos = st.multiselect("视频 (video_id)", video_ids, key="d_d_videos")
            f_modes = st.multiselect(
                "弹幕模式", [1, 4, 5, 7],
                format_func=lambda m: f"{m} · {DANMAKU_MODE_LABELS.get(m, '?')}",
                key="d_d_modes",
            )
        with c2:
            progress_range = st.slider(
                "视频内进度 (秒)", 0, 7200, (0, 7200), step=60, key="d_d_prog",
            )
            f_kw = st.text_input("弹幕内容关键词", "", key="d_d_kw")
        with c3:
            st.caption("💡 默认 0-7200s（2 小时）；拖动滑块可分段聚合")

    stmt = select(Danmaku)
    if f_videos:
        stmt = stmt.where(Danmaku.video_id.in_(f_videos))
    if f_modes:
        stmt = stmt.where(Danmaku.mode.in_(f_modes))
    if progress_range != (0, 7200):
        stmt = stmt.where(Danmaku.progress.between(*progress_range))
    if f_kw:
        stmt = stmt.where(Danmaku.content.contains(f_kw))

    stmt = stmt.order_by(Danmaku.id.desc())

    df, total = _paginated_query(session, stmt, page_size, page_num)
    _render_table_with_csv(
        df, total, page_size, page_num, "danmaku",
        display_cols=[
            "id", "video_id", "progress", "mode", "color",
            "user_hash", "posted_at", "fetched_at", "content",
        ],
    )


def _render_details_bilibili_queue(repo, page_size: int, page_num: int) -> None:
    """bilibili_queue 表 — 主维度 = 状态 / due_date / 重访 / 失败次数 / 关键词"""
    session = repo.session

    statuses = ["pending", "scheduled", "fetching", "fetched", "failed"]

    with st.expander("🔧 筛选条件", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            f_status = st.multiselect("状态", statuses, key="d_q_status")
            f_revisit = st.selectbox(
                "重访标记 (revisit)", ["全部", "是", "否"], key="d_q_rev"
            )
        with c2:
            due_range = st.date_input(
                "due_date 范围（留空 = 不限）",
                value=(),
                key="d_q_due",
                help="单值 = 当天；两个值 = 区间（闭区间）",
            )
        with c3:
            f_kw = st.text_input(
                "BV 号 / 标题 / 备注 关键词", "", key="d_q_kw"
            )
            f_fail_min = st.number_input(
                "失败次数 ≥", min_value=0, value=0, step=1, key="d_q_fc"
            )

    stmt = select(BilibiliQueue)
    if f_status:
        stmt = stmt.where(BilibiliQueue.status.in_(f_status))
    if f_revisit == "是":
        stmt = stmt.where(BilibiliQueue.revisit.is_(True))
    elif f_revisit == "否":
        stmt = stmt.where(BilibiliQueue.revisit.is_(False))
    # date_input 返回类型：tuple(date) 或单 date；统一转 tuple
    if isinstance(due_range, tuple) and len(due_range) == 2:
        s_date, e_date = due_range
        stmt = stmt.where(
            BilibiliQueue.due_date.between(
                datetime.combine(s_date, datetime.min.time()),
                datetime.combine(e_date, datetime.max.time()),
            )
        )
    elif isinstance(due_range, tuple) and len(due_range) == 1:
        # 单日期：当日 0 点 ~ 23:59:59
        s_date = due_range[0]
        stmt = stmt.where(
            BilibiliQueue.due_date.between(
                datetime.combine(s_date, datetime.min.time()),
                datetime.combine(s_date, datetime.max.time()),
            )
        )
    elif hasattr(due_range, "year") and not isinstance(due_range, tuple):
        s_date = due_range
        stmt = stmt.where(
            BilibiliQueue.due_date.between(
                datetime.combine(s_date, datetime.min.time()),
                datetime.combine(s_date, datetime.max.time()),
            )
        )
    if f_kw:
        stmt = stmt.where(
            or_(
                BilibiliQueue.bv_id.contains(f_kw),
                BilibiliQueue.title.contains(f_kw),
                BilibiliQueue.note.contains(f_kw),
            )
        )
    if f_fail_min > 0:
        stmt = stmt.where(BilibiliQueue.fail_count >= f_fail_min)

    # due_date 为 NULL 的排最后（待识别/已废弃），其余倒序
    stmt = stmt.order_by(
        BilibiliQueue.due_date.desc().nullslast(),
        BilibiliQueue.id.desc(),
    )

    df, total = _paginated_query(session, stmt, page_size, page_num)
    _render_table_with_csv(
        df, total, page_size, page_num, "bilibili_queue",
        display_cols=[
            "id", "bv_id", "title", "status", "pubdate", "due_date",
            "added_at", "fetched_at", "comment_count", "danmaku_count",
            "fail_count", "fail_reason", "revisit", "note",
        ],
    )


def render_compare(repo) -> None:
    """P3 多目标横向对比视图（图表优先，独立于单目标看板）。"""
    targets = repo.list_targets(platform="steam")
    if not targets:
        st.info("暂无 Steam 目标数据，请先运行采集与分析流水线。")
        return

    name_of = {t["target_id"]: t["name"] for t in targets}
    all_ids = [t["target_id"] for t in targets]

    st.subheader("多目标横向对比")
    st.caption("口径：情感 = 评论级；主题 = 观点级（默认排除「综合与元表达」兜底桶）。")

    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        selected_ids = st.multiselect(
            "对比游戏", all_ids, default=all_ids[:10],
            format_func=lambda t: name_of.get(t, t),
        )
    with c2:
        heatmap_mode = st.radio("热力图模式", ["占比", "偏差"], index=0, key="hm_mode")
    with c3:
        level = st.selectbox("主题粒度", ["L1", "L2", "L3"], index=0)

    if not selected_ids:
        st.info("请至少选择一款游戏。")
        return
    selected = [t for t in targets if t["target_id"] in selected_ids]

    # 概览 KPI 表
    kpi_rows = []
    for t in selected:
        total_sent = t["pos"] + t["neg"] + t["neu"]
        kpi_rows.append({
            "游戏": t["name"],
            "样本": t["total"],
            "推荐率%": t["recommend_rate"] if t["recommend_rate"] is not None else "—",
            "情感均分": round(t["avg_score"], 2) if t["avg_score"] is not None else "—",
            "正面%": round(t["pos"] / total_sent * 100, 1) if total_sent else 0,
            "负面%": round(t["neg"] / total_sent * 100, 1) if total_sent else 0,
        })
    st.dataframe(pd.DataFrame(kpi_rows), use_container_width=True, hide_index=True)

    # 口碑定位散点
    st.subheader("口碑定位（X=推荐率 · Y=情感均分 · 气泡=样本量）")
    st.plotly_chart(position_scatter(pd.DataFrame(selected)), use_container_width=True)

    # 情感构成堆叠条
    st.subheader("情感构成（评论级）")
    ratio_df = repo.sentiment_ratio_by_targets(selected_ids)
    if ratio_df.empty:
        st.info("暂无情感数据")
    else:
        ratio_df["name"] = ratio_df["target_id"].map(name_of)
        st.plotly_chart(sentiment_stacked(ratio_df), use_container_width=True)

    # 主题 × 游戏 热力图
    st.subheader("主题 × 游戏（观点级）")
    matrix = repo.opinion_matrix(selected_ids, level=level)
    if matrix.empty:
        st.info("暂无观点数据")
    else:
        mode = "deviation" if heatmap_mode == "偏差" else "ratio"
        st.plotly_chart(topic_game_heatmap(matrix, name_of, mode=mode), use_container_width=True)

    # 负面痛点表
    st.subheader("负面痛点 TOP5（L2）")
    pain = repo.negative_pain_points(selected_ids, level="L2", top=5)
    tabs = st.tabs([t["name"] for t in selected])
    for tab, t in zip(tabs, selected):
        rows = pain.get(t["target_id"], [])
        with tab:
            if rows:
                st.table(pd.DataFrame(rows, columns=["L2 路径", "负面观点数"]))
            else:
                st.caption("无负面观点")

    # 下钻到单目标看板
    st.subheader("下钻到单目标看板")
    d1, d2 = st.columns([3, 1])
    with d1:
        drill_name = st.selectbox("选择游戏", [t["name"] for t in selected], key="drill_name")
    with d2:
        if st.button("打开单目标看板"):
            picked = next(t for t in selected if t["name"] == drill_name)
            st.session_state["_drill_target"] = picked["target_id"]
            st.rerun()


st.set_page_config(
    page_title="灵听 · Lynx",
    page_icon=str(LOGO_PATH),
    layout="wide",
    initial_sidebar_state="expanded",
)

st.sidebar.image(str(LOGO_PATH), width=48)
st.sidebar.caption("灵听 · Lynx")
st.title("灵听 · Lynx")
st.markdown("""
> **个人项目** —— 基于多平台数据的消费者反馈采集、情感分析与可视化  
> 数据源：Steam 公开评测 · 分析引擎：DeepSeek/Qwen/GLM  
""")

# === 消费下钻请求（必须在 radio 实例化前，否则触发 widget state 冲突） ===
_drill_tid = st.session_state.pop("_drill_target", None)
if _drill_tid:
    st.session_state["view"] = "单目标看板"
    st.session_state["platform"] = "steam"
    st.session_state["selected_target"] = _drill_tid

# === 视图切换 ===
with st.sidebar:
    view = st.radio(
        "视图",
        ["单目标看板", "多目标对比", "📋 明细核查"],
        index=0,
        key="view",
        help="单目标：聚焦一款游戏的情感/主题/样本；多目标：横向对比多款游戏的口碑与痛点；明细核查：按 DB 表切换 + 多维筛选 + 分页浏览 + CSV 导出（与 DB Browser 互补）",
    )

if view == "多目标对比":
    engine, SessionLocal = init_db()
    session = SessionLocal()
    render_compare(CommentRepository(session))
    st.stop()

if view == "📋 明细核查":
    engine, SessionLocal = init_db()
    session = SessionLocal()
    render_details(CommentRepository(session))
    st.stop()

# === 侧边栏 ===
with st.sidebar:
    st.header("⚙️ 数据筛选")
    platform = st.selectbox("数据来源平台", ["all", "steam", "bilibili"], index=1, key="platform")

    engine, SessionLocal = init_db()
    session = SessionLocal()
    repo = CommentRepository(session)

    total = repo.count(platform=None if platform == "all" else platform)
    analyzed = len(repo.all_analyzed(platform=None if platform == "all" else platform))
    st.metric("评论总数", total)
    st.metric("已分析数", analyzed)

    target_options = ["全部"]
    _targets = repo.list_targets(platform=None if platform == "all" else platform)
    target_options += [t["target_id"] for t in _targets]

    # 下钻 / 平台切换后，确保 selected_target 仍在选项内
    if st.session_state.get("selected_target") not in target_options:
        st.session_state["selected_target"] = "全部"
    selected_target = st.selectbox("目标对象", target_options, key="selected_target")

st.divider()

# === 数据加载 ===
if selected_target == "全部":
    comments = repo.all_analyzed(platform=None if platform == "all" else platform, limit=2000)
else:
    pf, tid = selected_target.split(":", 1)
    comments = repo.list_by_target(pf, tid, limit=2000)

df = build_summary_dataframe(comments)

if df.empty:
    st.info("""
    📭 **暂无分析数据**

    请先运行采集与分析流水线：

    ```bash
    python -m src.pipeline --platform steam --target 730 --count 50
    ```

    或在侧边栏选择其他数据源。
    """)
    st.stop()

# === 概览指标 ===
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("评论数", len(df))
with col2:
    pos_ratio = (df["sentiment"] == "positive").sum() / len(df) * 100
    st.metric("正面占比", f"{pos_ratio:.1f}%")
with col3:
    neg_ratio = (df["sentiment"] == "negative").sum() / len(df) * 100
    st.metric("负面占比", f"{neg_ratio:.1f}%")
with col4:
    avg_score = df["sentiment_score"].mean()
    st.metric("情感均分", f"{avg_score:+.2f}")

st.divider()

# === 第一行：情感分布饼图 + 主题分布柱状图 ===
col1, col2 = st.columns(2)

with col1:
    st.subheader("📊 情感分布")
    sent_dist = sentiment_distribution(df)
    if not sent_dist.empty:
        colors = {"positive": "#52c41a", "negative": "#ff4d4f", "neutral": "#8c8c8c"}
        fig = px.pie(
            sent_dist,
            values="count",
            names="sentiment",
            color="sentiment",
            color_discrete_map=colors,
            hole=0.4,
        )
        fig.update_traces(textposition="inside", textinfo="label+percent")
        st.plotly_chart(fig, use_container_width=True)

with col2:
    st.subheader("🏷️ 主题分布 TOP10")
    topic_dist = topic_distribution(df).head(10)
    if not topic_dist.empty:
        fig = px.bar(
            topic_dist,
            x="count",
            y="topic",
            orientation="h",
            color="count",
            color_continuous_scale="Blues",
        )
        fig.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig, use_container_width=True)

st.divider()

# === 第二行：词云 + 情感分数分布 ===
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("☁️ 评论关键词词云")
    with st.spinner("正在生成词云..."):
        contents = df["content"].dropna().tolist()
        keywords = extract_keywords(contents, top_k=80)
        if keywords:
            font_path = _find_chinese_font()
            if font_path is None:
                st.warning("⚠️ 未检测到中文字体，词云中文可能显示为方框")
            wc = WordCloud(
                font_path=font_path,
                width=800,
                height=400,
                background_color="white",
                max_words=80,
            )
            wc.generate_from_frequencies(dict(keywords))
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.imshow(wc, interpolation="bilinear")
            ax.axis("off")
            st.pyplot(fig)
        else:
            st.info("无可用文本")

with col2:
    st.subheader("📈 情感分数分布")
    fig = px.histogram(
        df,
        x="sentiment_score",
        nbins=30,
        color="sentiment",
        color_discrete_map={"positive": "#52c41a", "negative": "#ff4d4f", "neutral": "#8c8c8c"},
    )
    fig.update_layout(xaxis_title="情感分数（-1 ~ +1）", yaxis_title="评论数")
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# === 典型评论展示 ===
st.subheader("💬 典型评论样本")
filter_sentiment = st.multiselect(
    "筛选情感",
    options=["positive", "neutral", "negative"],
    default=["positive", "negative"],
)

filtered = df[df["sentiment"].isin(filter_sentiment)].sort_values(
    "sentiment_confidence", ascending=False
)

for _, row in filtered.head(10).iterrows():
    emoji = {"positive": "😊", "negative": "😞", "neutral": "😐"}.get(row["sentiment"], "💬")
    with st.container(border=True):
        col1, col2 = st.columns([4, 1])
        with col1:
            st.markdown(f"{emoji} {row['content'][:300]}{'...' if len(row['content']) > 300 else ''}")
        with col2:
            st.caption(f"**{row.get('topic', '其他')}**")
            st.caption(f"分数 {row['sentiment_score']:+.2f}")
            st.caption(f"置信度 {row['sentiment_confidence']:.2f}")

st.divider()

# === 底部信息 ===
st.caption("""
📌 **数据说明**  
本项目仅采集公开可见内容用于学习研究，不存储任何用户隐私信息。  
所有数据均来自各平台官方API或公开页面。  
📦 技术栈：Python · Streamlit · SQLAlchemy · DeepSeek/Qwen
""")
