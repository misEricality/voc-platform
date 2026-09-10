"""项目文档语料 + 检索（2026-09-09 · 见 docs/architecture/ORIGINAL_VOICE_ANALYSIS_AGENT.md §4）

职责：
- 启动期一次性切分 5 个核心 .md（按 `##` 标题切分小节）
- 给定 query，对每节计算 BM25-lite 评分（分词 + 词频 / 章节长度）
- 返回 top_k 节（path + heading + snippet）

为什么不接现成 RAG：
- 体量小（5 docs / ~150 KB），分词后只有几千节
- 复杂度低（避免引入 faiss / rank_bm25 依赖）
- 离线可跑（不依赖网络/LLM）

可扩展：之后若 docs 数 > 50 或单 doc > 1MB，再换 faiss/elasticsearch。
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("voc.agent.corpus")

# Agent tool 的检索范围：5 个最常被问的 doc
# （不用全部 33 个 .md；太多会拖累且噪声大）
CORPUS_FILES: list[Path] = [
    Path("docs/architecture/ORIGINAL_VOICE_ANALYSIS_AGENT.md"),  # Agent 设计本身
    Path("AGENTS.md"),                                          # 工程约定
    Path("docs/00-index.md"),                                   # 文档地图
    Path("README.md"),                                          # 项目总览
    Path("docs/architecture/WEB_DASHBOARD.md"),                 # 看板设计
]

# 中文停用词（够用即可：避免无意义词拉低信号；不进 stopwords 包保持零依赖）
_STOPWORDS = frozenset({
    "的", "了", "在", "是", "和", "与", "或", "及", "等", "之", "为", "以",
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "它们",
    "什么", "怎么", "如何", "为什么", "哪些", "那个", "这个",
    "一下", "一种", "这个", "那个", "这里", "那里",
    "啊", "哦", "嗯", "吗", "呢", "吧", "嘛",
})


def _tokenize(text: str) -> list[str]:
    """中文按字 + 英文按词 + 数字整段保留

    - 中文 2~4 字窗口（粗粒度；不引 jieba 保持零依赖）
    - 英文 \\w+
    - 数字 \\d+
    - 全部转小写、去停用词、去单字
    """
    text = text.lower()
    # 英文/数字
    en_tokens = re.findall(r"[a-z0-9]+", text)
    # 中文 2~4 字滑窗
    zh_part = re.sub(r"[a-z0-9\s]+", " ", text)
    zh_tokens = []
    for n in (2, 3, 4):
        for i in range(len(zh_part) - n + 1):
            tk = zh_part[i:i + n]
            if tk and not all(c in _STOPWORDS for c in tk) and any('\u4e00' <= c <= '\u9fff' for c in tk):
                zh_tokens.append(tk)
    out = [t for t in en_tokens + zh_tokens if t and t not in _STOPWORDS and len(t) > 1]
    return out


def _split_sections(md_text: str) -> list[dict]:
    """按 `^## ` 切分（不含一级标题，单独保留为 doc 标题）

    Returns: [{"heading": "...", "body": "..."}, ...]
    """
    lines = md_text.split("\n")
    sections: list[dict] = []
    cur_heading = "(开头)"
    cur_body: list[str] = []
    for line in lines:
        if re.match(r"^##\s+", line):
            if cur_body:
                sections.append({"heading": cur_heading, "body": "\n".join(cur_body)})
            cur_heading = line.lstrip("#").strip()
            cur_body = []
        else:
            cur_body.append(line)
    if cur_body:
        sections.append({"heading": cur_heading, "body": "\n".join(cur_body)})
    return sections


@lru_cache(maxsize=1)
def load_corpus() -> list[dict]:
    """加载语料（一次性；lru_cache 让后续调用零 IO）

    Returns: [{path, heading, body, tokens: list[str], len: int}, ...]
    """
    repo_root = Path(__file__).resolve().parents[2]
    corpus: list[dict] = []
    for rel_path in CORPUS_FILES:
        full = repo_root / rel_path
        if not full.exists():
            log.warning(f"corpus file missing: {rel_path}")
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = full.read_text(encoding="gbk", errors="ignore")
        for sec in _split_sections(text):
            tokens = _tokenize(sec["heading"] + " " + sec["body"][:1500])  # 截断防巨大
            if not tokens:
                continue
            corpus.append({
                "path": str(rel_path),
                "heading": sec["heading"],
                "body": sec["body"],
                "tokens": tokens,
                "len": len(tokens),
            })
    log.info(f"md_corpus loaded: {len(corpus)} sections from {len(CORPUS_FILES)} docs")
    return corpus


def search(query: str, top_k: int = 3) -> list[dict]:
    """检索最相关的 top_k 节（BM25-lite 评分）

    Score = Σ_tf(t) / (len(d) + 1) * idf-like boost（简化版）

    Returns: [{path, heading, snippet, score}, ...]
    """
    if not query or not query.strip():
        return []

    q_tokens = _tokenize(query)
    if not q_tokens:
        return []
    q_set = set(q_tokens)

    corpus = load_corpus()
    if not corpus:
        return []

    scored: list[tuple[float, dict]] = []
    for sec in corpus:
        # 词频
        tf: dict[str, int] = {}
        for t in sec["tokens"]:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        matched_terms: list[str] = []
        for qt in q_set:
            c = tf.get(qt, 0)
            if c:
                # BM25-lite: tf / (len + 1) 简化（无 k1/b 参数）
                score += c / (sec["len"] + 1)
                matched_terms.append(qt)
        # 标题命中加权
        heading_tokens = set(_tokenize(sec["heading"]))
        heading_hits = len(q_set & heading_tokens)
        if heading_hits:
            score *= (1 + 0.5 * heading_hits)

        if score > 0:
            scored.append((score, {**sec, "matched": matched_terms}))

    scored.sort(key=lambda x: -x[0])
    out = []
    for score, sec in scored[:top_k]:
        snippet = sec["body"].strip()[:400]
        out.append({
            "path": sec["path"],
            "heading": sec["heading"],
            "snippet": snippet,
            "matched_terms": sec["matched"],
            "score": round(score, 6),
        })
    return out
