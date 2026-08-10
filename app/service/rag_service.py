"""混合检索服务：向量 + BM25 + RRF 融合 + 距离阈值门控。

- vector：纯向量检索，可用 max_distance 做相关度门控（拒绝距离过大的无关查询）；
- hybrid：向量 top-N 与 BM25 top-N 用 RRF（倒数排名融合）合并排序，
  再用底层向量距离做门控，兼顾语义与关键词命中。
"""

import re
from functools import lru_cache

import jieba
from rank_bm25 import BM25Okapi

from app.service.vectorstore_service import get_vectorstore

# 向量/BM25 各自取前 N 再融合；RRF 常数（越大越平滑）
_TOP_N = 20
_RRF_K = 60


def _tokenize(text: str) -> list[str]:
    """中文分词（jieba）+ 小写，供 BM25 使用。"""
    return [token for token in jieba.lcut(text.lower()) if re.search(r"\w", token)]


def _chunk_id(meta: dict) -> str:
    """从元数据重建 chunk id（入库时的 doc_id--NN 格式）。"""
    return f"{meta.get('doc_id', '')}--{int(meta.get('chunk_index', 0)):02d}"


@lru_cache(maxsize=1)
def _bm25_index():
    """从 Chroma 集合读出全部 chunk 构建 BM25 语料（惰性、单例）。"""
    collection = get_vectorstore()._collection
    data = collection.get(include=["documents", "metadatas"])
    documents = data["documents"] or []
    metadatas = data["metadatas"] or []
    corpus = [_tokenize(document) for document in documents]
    return BM25Okapi(corpus), documents, metadatas


def _to_item(doc, distance: float | None, meta: dict | None = None) -> dict:
    """把检索结果归一成统一字典。"""
    meta = doc.metadata if meta is None else meta
    # BM25 路径的 doc 是纯文本字符串，向量路径是 Document 对象
    content = meta.get("content")
    if content is None:
        content = doc.page_content if hasattr(doc, "page_content") else doc
    return {
        "chunk_id": _chunk_id(meta),
        "doc_id": meta.get("doc_id"),
        "source": meta.get("source"),
        "content": content,
        "distance": distance,
    }


def _rrf(*ranked_lists: list[dict]) -> list[dict]:
    """倒数排名融合：score = Σ 1/(K + rank)，按总分降序返回去重结果。"""
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            chunk_id = item["chunk_id"]
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
            items.setdefault(chunk_id, item)
    ordered = sorted(scores, key=scores.get, reverse=True)
    return [items[chunk_id] for chunk_id in ordered]


def retrieve(
    query: str,
    k: int = 5,
    retriever: str = "vector",
    max_distance: float | None = None,
) -> list[dict]:
    """检索 top-k；max_distance 存在时门用底层向量距离做相关度控。"""
    vectorstore = get_vectorstore()
    if retriever == "vector":
        raw = vectorstore.similarity_search_with_score(query, k=_TOP_N)
        items = [_to_item(doc, distance) for doc, distance in raw]
        if max_distance is not None:
            items = [item for item in items if item["distance"] <= max_distance]
        return items[:k]

    # hybrid：向量与 BM25 各自 top-N，RRF 融合后再门控
    raw = vectorstore.similarity_search_with_score(query, k=_TOP_N)
    vector_items = [_to_item(doc, distance) for doc, distance in raw]

    bm25, documents, metadatas = _bm25_index()
    scores = bm25.get_scores(_tokenize(query))
    top_indices = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)[:_TOP_N]
    bm25_items = [
        _to_item(documents[index], None, metadatas[index])
        for index in top_indices
        # 只保留真实关键词命中（分数>0），避免零分噪声穿过门控
        if documents[index] and scores[index] > 0
    ]

    fused = _rrf(vector_items, bm25_items)
    if max_distance is not None:
        # BM25 单独命中的 chunk 没有向量距离，视为通过门控
        fused = [
            item
            for item in fused
            if item["distance"] is None or item["distance"] <= max_distance
        ]
    return fused[:k]
