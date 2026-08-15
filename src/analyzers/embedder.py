"""本地 embedding 模块（2026-08-11）

职责：评论文本 → 语义向量（供语义检索 / 聚类 / 观点去重使用）。

设计要点：
- 模型：`BAAI/bge-small-zh-v1.5`（512 维，中文，~95MB，本地 CPU 推理，零 API 成本）。
  通过环境变量 `EMBEDDING_MODEL` 覆盖（模型标识必须含小版本号，换模型 = 全量重算）。
- 依赖：sentence-transformers（可选）。未安装时 `get_embedder()` 返回 None，
  调用方（pipeline / 脚本）跳过向量化，与分析器初始化失败同模式，不阻塞主流程。
- 输出约定：L2 归一化后的 float32 向量（与 storage 层一致：内积 = 余弦相似度）。
- 单例：模型只加载一次，避免 pipeline 内每条评论重复加载。
- 选型理由：数据 100% 中文短评 + 本地 CPU，bge-small-zh-v1.5 性价比最优；
  BGE-M3 的多语言/长文本优势当前场景用不上，且 2.35GB 会放大重算成本（见 2026-08-11 决策）。
"""

from __future__ import annotations

import logging
import os

import numpy as np

# 默认模型；可通过 .env 覆盖。标识含小版本号，避免同名模型权重更新被误判为同一模型。
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

_log = logging.getLogger("voc.embedder")


class LocalEmbedder:
    """基于 sentence-transformers 的本地 embedding 编码器"""

    name = "local"

    def __init__(self, model_name: str = MODEL_NAME):
        # 延迟导入：未安装 sentence-transformers 时在构造处抛出，由 get_embedder 捕获
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)
        self.model_name = model_name
        # sentence-transformers 新版本重命名了维度接口，兼容两者
        dim_getter = getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension
        self.dim: int = int(dim_getter())

    def encode_batch(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        """批量编码，返回 (N, dim) 的 L2 归一化 float32 矩阵"""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vecs = self.model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,  # L2 归一化：内积 = 余弦
            show_progress_bar=False,
        )
        return np.asarray(vecs, dtype=np.float32)

    def encode(self, text: str) -> np.ndarray:
        """单条编码，返回 (dim,) 向量"""
        return self.encode_batch([text])[0]


_embedder: LocalEmbedder | None = None
_embedder_loaded = False


def get_embedder() -> LocalEmbedder | None:
    """获取单例 embedder；依赖缺失时返回 None（调用方应跳过向量化）"""
    global _embedder, _embedder_loaded
    if _embedder_loaded:
        return _embedder
    _embedder_loaded = True
    try:
        _embedder = LocalEmbedder()
        _log.info("embedding 模型加载完成: %s (dim=%d)", _embedder.model_name, _embedder.dim)
    except Exception as e:  # noqa: BLE001 - 依赖缺失/下载失败均降级
        _log.warning("embedder 初始化失败，本次跳过向量化: %s", e)
        _embedder = None
    return _embedder


def semantic_search(repo, query: str, top_k: int = 5, model: str | None = None) -> list[dict]:
    """语义检索：query → top_k 相关评论（需要表内已有向量）

    Args:
        repo: CommentRepository 实例
        query: 自然语言查询（如"服务器掉线""打击感"）
        top_k: 返回条数
        model: 限定模型标识；不传则取表内实际模型

    Returns:
        [{"comment_id", "content", "score", "target_id", "sentiment"}, ...]（按相似度降序）
    """
    emb = get_embedder()
    if emb is None:
        raise RuntimeError("embedder 不可用（未安装 sentence-transformers）")

    # 单空间断言：混合向量空间禁止检索（防线 3）
    models = repo.embedding_models_in_use()
    if not models:
        return []
    if model is None:
        model = models[0]
    if set(models) != {model}:
        raise RuntimeError(f"向量空间不一致（表内 {models}，检索限定 {model}），请先全量重算")

    qv = emb.encode(query)
    matrix, ids = repo.load_embedding_matrix(model=model)
    if not ids:
        return []
    sims = matrix @ qv
    order = np.argsort(sims)[::-1][:top_k]

    id2sim = {ids[i]: float(sims[i]) for i in order}
    comments = repo.get_comments_by_ids([ids[i] for i in order])
    by_id = {c.id: c for c in comments}
    results = []
    for cid in [ids[i] for i in order]:
        c = by_id.get(cid)
        if c is None:
            continue
        results.append(
            {
                "comment_id": cid,
                "content": c.content,
                "score": round(id2sim[cid], 4),
                "target_id": c.target_id,
                "sentiment": c.sentiment,
                "topic": c.topic,
            }
        )
    return results
