"""DashScope text-embedding-v4 封装：文本向量化 + 余弦相似度。

Embedding 用于 RAG 检索：知识片段入库时向量化，查询时把问题向量化后算相似度。
基于 langchain_community 的 DashScopeEmbeddings（原生 dashscope SDK），
模型默认 text-embedding-v4，可通过 EMBEDDING_MODEL 环境变量覆盖。
"""

import os

from dotenv import load_dotenv
from dashscope import TextEmbedding
from langchain_community.embeddings import DashScopeEmbeddings

from app.models.ai_call_record import AiOperation
from app.service.ai_resilience import policy_for

load_dotenv()

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
# DashScope 兼容端点单次请求建议不超过 10 条文本，超出自动分批
_BATCH_SIZE = 10
# 惰性创建的单例客户端
_EMBEDDINGS_CLIENT: DashScopeEmbeddings | None = None


class _TimedTextEmbedding:
    """为 LangChain 的 DashScope 适配器补上统一的请求超时。"""

    @classmethod
    def call(cls, **kwargs):
        kwargs.setdefault(
            "request_timeout",
            policy_for(AiOperation.RAG_EMBEDDING).timeout_seconds,
        )
        return TextEmbedding.call(**kwargs)


def _get_embeddings() -> DashScopeEmbeddings:
    """创建（或复用）DashScope embedding 客户端。"""
    global _EMBEDDINGS_CLIENT
    if _EMBEDDINGS_CLIENT is None:
        client = DashScopeEmbeddings(
            model=EMBEDDING_MODEL,
            dashscope_api_key=os.getenv("DASHSCOPE_API_KEY"),
            # 外层统一策略决定是否重试；这里的 1 表示只尝试一次。
            max_retries=1,
        )
        # DashScopeEmbeddings 的 Pydantic validator 会无条件覆盖构造参数里的
        # client，因此实例创建后再换成带超时的兼容适配器。
        client.client = _TimedTextEmbedding
        _EMBEDDINGS_CLIENT = client
    return _EMBEDDINGS_CLIENT


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化文本（自动分批），返回与输入顺序一致的向量列表。"""
    embeddings = _get_embeddings()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[start : start + _BATCH_SIZE]
        vectors.extend(embeddings.embed_documents(batch))
    return vectors


def embed_query(text: str) -> list[float]:
    """向量化单条查询。"""
    return _get_embeddings().embed_query(text)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度：向量夹角越小越相似，用于检索结果排序。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
