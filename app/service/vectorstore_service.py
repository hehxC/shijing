"""领域知识向量库：Chroma + DashScopeEmbeddings。

知识片段入库时切分 + 向量化写入 Chroma（本地持久化 data/chroma/），
查询时用 embedding 算相似度召回相关片段。供 RAG 检索节点使用。
"""

import os
from pathlib import Path

from langchain_chroma import Chroma

from app.service.embedding_service import _get_embeddings

# 集合名与持久化目录，均可用环境变量覆盖
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "garden_knowledge")
DB_PATH = Path(os.getenv("CHROMA_PERSIST_DIR", "data/chroma"))

# 惰性创建的单例向量库
_VECTORSTORE: Chroma | None = None


def get_vectorstore() -> Chroma:
    """创建（或复用）Chroma 向量库实例。"""
    global _VECTORSTORE
    if _VECTORSTORE is None:
        DB_PATH.mkdir(parents=True, exist_ok=True)
        _VECTORSTORE = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=_get_embeddings(),
            persist_directory=str(DB_PATH),
        )
    return _VECTORSTORE


def add_knowledge(
    texts: list[str],
    metadatas: list[dict] | None = None,
    ids: list[str] | None = None,
) -> None:
    """把知识片段（带元数据）写入向量库。"""
    get_vectorstore().add_texts(texts, metadatas=metadatas, ids=ids)


def search(query: str, k: int = 5) -> list[tuple]:
    """按相似度检索 top-k 片段，返回 [(文档, 相似度分), ...]。"""
    return get_vectorstore().similarity_search_with_score(query, k=k)
