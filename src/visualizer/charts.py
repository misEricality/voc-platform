"""可视化辅助函数

供 Streamlit Dashboard 复用。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

import jieba
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


# 中文停用词（精简版，覆盖常见无意义词）
STOPWORDS = set(
    """
    的 了 是 在 和 与 及 或 也 就 都 还 但 而 但 反 而且
    这 那 这个 那个 这些 那些 一个 一些 什么 怎么 为什么
    我 你 他 她 它 我们 你们 他们 它们
    不 没 没有 不太 不很 不是 不算
    很 太 真 真的 非常 特别 比较 觉得 感觉 一直
    上 下 里 外 前 后 中 内
    能 会 可以 应该 需要
    啊 吧 呢 嗯 哦 哈 哈哈 呵呵 嘻嘻
    就是 就是说 反正 然后 那么 因为 所以 因此
    对 对于 关于 按照 通过
    自己 各 每 所有 全部 大部分 一些
    把 被 让 给 向 从 到
    steam 游戏 这游戏 这作 这个 这个游戏 这个游戏
    一点 一样 一些 下 下次 上次 一开始
    """.split()
)


def _clean_text(text: str) -> str:
    """清洗文本：去除标点、特殊字符"""
    if not text:
        return ""
    text = re.sub(r"http\S+", " ", text)  # URL
    text = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", text)  # 保留中文与基本字符
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_keywords(texts: Iterable[str], top_k: int = 50) -> list[tuple[str, int]]:
    """提取高频词

    Args:
        texts: 文本列表
        top_k: 返回前N个关键词

    Returns:
        [(word, count), ...]
    """
    counter: Counter = Counter()
    for t in texts:
        cleaned = _clean_text(t)
        if not cleaned:
            continue
        for word in jieba.cut(cleaned):
            w = word.strip()
            if len(w) < 2 or w.lower() in STOPWORDS:
                continue
            counter[w] += 1
    return counter.most_common(top_k)


def build_summary_dataframe(comments: list) -> pd.DataFrame:
    """将 Comment ORM 对象列表转为 DataFrame"""
    rows = [c.to_dict() for c in comments]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "posted_at" in df.columns:
        df["posted_at"] = pd.to_datetime(df["posted_at"], errors="coerce")
    return df


def sentiment_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """情感分布统计"""
    if df.empty or "sentiment" not in df.columns:
        return pd.DataFrame(columns=["sentiment", "count", "ratio"])
    dist = df["sentiment"].value_counts().reset_index()
    dist.columns = ["sentiment", "count"]
    total = dist["count"].sum()
    dist["ratio"] = (dist["count"] / total * 100).round(1) if total else 0
    return dist


def topic_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """主题分布统计"""
    if df.empty or "topic" not in df.columns:
        return pd.DataFrame(columns=["topic", "count"])
    topic_df = df.dropna(subset=["topic"])
    return (
        topic_df["topic"]
        .value_counts()
        .rename_axis("topic")
        .reset_index(name="count")
    )


def format_datetime(dt) -> str:
    if dt is None or pd.isna(dt):
        return "—"
    if isinstance(dt, str):
        return dt
    return dt.strftime("%Y-%m-%d %H:%M")


# ============================================================
# P3 多目标对比 —— 图表
# ============================================================

SENTIMENT_COLORS = {"positive": "#52c41a", "negative": "#ff4d4f", "neutral": "#8c8c8c"}
SENTIMENT_LABELS = {"positive": "正面", "neutral": "中性", "negative": "负面"}


def sentiment_stacked(ratio_df: pd.DataFrame) -> go.Figure:
    """100% 堆叠情感条（一行一游戏，正/中/负三段）。

    ratio_df 需含列：name / positive / neutral / negative（百分比）。
    """
    fig = go.Figure()
    for senti in ("positive", "neutral", "negative"):
        fig.add_trace(go.Bar(
            name=SENTIMENT_LABELS[senti],
            y=ratio_df["name"],
            x=ratio_df[senti],
            orientation="h",
            marker_color=SENTIMENT_COLORS[senti],
            text=ratio_df[senti].map(lambda v: f"{v:.0f}%"),
            textposition="inside",
            hovertemplate="%{y} · %{x}%<extra></extra>",
        ))
    fig.update_layout(
        barmode="stack",
        height=max(240, 36 * len(ratio_df) + 70),
        xaxis_title="占比 %",
        yaxis_title=None,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=10, r=10, t=30, b=10),
    )
    return fig


def topic_game_heatmap(
    count_matrix: pd.DataFrame,
    names: dict[str, str] | None = None,
    mode: str = "ratio",
) -> go.Figure:
    """主题 × 游戏 热力图。

    count_matrix: 行=topic，列=target_id，值=观点计数。
    mode="ratio" → 列归一化占比（%）；"deviation" → 各游戏占比 − 全体均值（百分点）。
    """
    m = count_matrix.astype(float)
    totals = m.sum(axis=0).replace(0, 1)
    ratio = m.div(totals, axis=1) * 100
    if mode == "deviation":
        z = ratio.sub(ratio.mean(axis=1), axis=0)
        colorscale = "RdBu_r"
        label = "占比 − 均值 (pp)"
        zmid = 0
    else:
        z = ratio
        colorscale = "Blues"
        label = "占比 %"
        zmid = None

    x_labels = [names.get(c, c) for c in z.columns] if names else list(z.columns)
    fig = go.Figure(go.Heatmap(
        z=z.values,
        x=x_labels,
        y=z.index.tolist(),
        colorscale=colorscale,
        zmid=zmid,
        colorbar=dict(title=label),
        hovertemplate="%{y} · %{x}<br>%{z:.1f}<extra></extra>",
    ))
    fig.update_layout(
        height=max(320, 22 * len(z.index) + 120),
        xaxis_title=None,
        yaxis_title=None,
        margin=dict(l=10, r=10, t=20, b=40),
    )
    return fig


def position_scatter(targets_df: pd.DataFrame) -> go.Figure:
    """口碑定位散点：X=本地推荐率，Y=情感均分，气泡=样本量。"""
    fig = px.scatter(
        targets_df,
        x="recommend_rate",
        y="avg_score",
        size="total",
        text="name",
        hover_name="name",
        size_max=60,
        height=420,
        labels={"recommend_rate": "本地推荐率 %", "avg_score": "LLM 情感均分", "total": "样本量"},
    )
    fig.update_traces(textposition="top center")
    fig.update_layout(margin=dict(l=10, r=10, t=30, b=10))
    return fig