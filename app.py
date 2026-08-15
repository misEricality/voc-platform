"""Streamlit 仪表盘 - VoC Platform

启动：streamlit run app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from wordcloud import WordCloud
import matplotlib.pyplot as plt

from src.storage.db import init_db, CommentRepository
from src.visualizer.charts import (
    extract_keywords,
    build_summary_dataframe,
    sentiment_distribution,
    topic_distribution,
)


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


st.set_page_config(
    page_title="VoC Platform · 消费者之声洞察",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🎙️ VoC Platform · 消费者之声洞察")
st.markdown("""
> **个人项目** —— 基于多平台数据的消费者反馈采集、情感分析与可视化  
> 数据源：Steam 公开评测 · 分析引擎：DeepSeek/Qwen/GLM  
""")

# === 侧边栏 ===
with st.sidebar:
    st.header("⚙️ 数据筛选")
    platform = st.selectbox("数据来源平台", ["all", "steam", "bilibili"], index=1)

    engine, SessionLocal = init_db()
    session = SessionLocal()
    repo = CommentRepository(session)

    total = repo.count(platform=None if platform == "all" else platform)
    analyzed = len(repo.all_analyzed(platform=None if platform == "all" else platform))
    st.metric("评论总数", total)
    st.metric("已分析数", analyzed)

    target_options = ["全部"]
    _comments = repo.all_analyzed(platform=None if platform == "all" else platform, limit=1000)
    target_ids = sorted({c.target_id for c in _comments if c.target_id})
    target_options += target_ids

    selected_target = st.selectbox("目标对象", target_options)

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