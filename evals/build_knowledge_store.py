"""把长文档知识源切分 + 向量化写入 Chroma（任务 3）。

用法（在项目根目录）：
    uv run python evals/build_knowledge_store.py                          # 默认参数
    uv run python evals/build_knowledge_store.py --chunk-size 150 --chunk-overlap 30
    uv run python evals/build_knowledge_store.py --include-long-docs   # 实验：加入长文档
    uv run python evals/build_knowledge_store.py --no-rebuild

设计说明：
    - 知识源是长文档（evals/fixtures/knowledge_documents.py），入库前用
      RecursiveCharacterTextSplitter 切分，chunk_size / chunk_overlap 可配置，
      方便后续评测不同切分策略对召回的影响；
    - 每个 chunk 的元数据记录 doc_id / chunk_index / source / tags，
      原始文档正文存 metadata["content"]（正文本身即 chunk 文本）；
    - 默认只向量化正文 content；设置 EMBED_WITH_TAGS=1 时把标签拼进向量文本，
      供任务 6 用检索评测对比两种策略的召回；
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chromadb  # noqa: E402
from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402

from app.service.vectorstore_service import (  # noqa: E402
    COLLECTION_NAME,
    DB_PATH,
    add_knowledge,
    get_vectorstore,
)
from evals.fixtures.knowledge_documents import KNOWLEDGE_DOCUMENTS  # noqa: E402

# 长文档目录：目录下每个 .md 文件作为一篇长文档参与切分
LONG_DOCS_DIR = Path(__file__).resolve().parent / "fixtures" / "long_documents"


def _load_long_documents() -> list[dict]:
    """读取 long_documents 目录下的 .md 文件，每篇作为一篇长文档。"""
    documents = []
    for path in sorted(LONG_DOCS_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        documents.append(
            {
                "id": f"long-{path.stem}",
                "source": "综合指南",
                "tags": ["指南", "长文档"],
                "content": content,
            }
        )
    return documents


def build(
    rebuild: bool,
    chunk_size: int,
    chunk_overlap: int,
    include_long_docs: bool,
) -> None:
    """重建（可选）并把长文档切分后入库。"""
    if rebuild:
        # 直接删集合再重建，清掉旧数据和之前冒烟产生的残留
        client = chromadb.PersistentClient(path=str(DB_PATH))
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"已删除旧集合：{COLLECTION_NAME}")
        except Exception:
            print("旧集合不存在，直接重建")

    # 中文长文本切分：优先按段落、句子、分号、逗号断点，减少语义割裂
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    )
    with_tags = os.getenv("EMBED_WITH_TAGS") == "1"
    texts: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []
    all_documents = KNOWLEDGE_DOCUMENTS
    if include_long_docs:
        all_documents = all_documents + _load_long_documents()
    for document in all_documents:
        chunks = splitter.split_text(document["content"])
        for index, chunk in enumerate(chunks):
            # 向量化文本：默认只正文；EMBED_WITH_TAGS=1 时拼接标签增强词级命中
            text = chunk
            if with_tags:
                text = f"{text} 标签：{'、'.join(document['tags'])}"
            texts.append(text)
            metadatas.append(
                {
                    "doc_id": document["id"],
                    "chunk_index": index,
                    "chunk_count": len(chunks),
                    "source": document["source"],
                    "tags": "、".join(document["tags"]),
                    "content": chunk,
                }
            )
            ids.append(f"{document['id']}--{index:02d}")

    add_knowledge(texts, metadatas=metadatas, ids=ids)
    print(
        f"已入库 {len(texts)} 个 chunk（{len(all_documents)} 篇文档，"
        f"长文档={'开' if include_long_docs else '关'}，"
        f"chunk_size={chunk_size}，overlap={chunk_overlap}，EMBED_WITH_TAGS={with_tags}）"
    )

    # 验证：集合内文档数与种子集一致，抽样检索相关片段排前
    client = chromadb.PersistentClient(path=str(DB_PATH))
    count = client.get_collection(COLLECTION_NAME).count()
    print("集合内文档数:", count)
    assert count == len(texts), "入库数量不一致"
    assert count > len(all_documents), "切分未生效，chunk 数应大于文档数"

    results = get_vectorstore().similarity_search("新中式庭院设计有什么特点", k=3)
    for index, document in enumerate(results, 1):
        print(
            f"  第{index}名 {document.metadata.get('doc_id')}"
            f"#{document.metadata.get('chunk_index')} [{document.metadata.get('source')}] "
            f"{document.metadata.get('content', '')[:22]}..."
        )
    assert "新中式" in results[0].metadata.get("content", ""), "相关片段应排第一"
    print("验证通过：切分入库完成，检索召回正常")


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="知识库入库脚本")
    parser.add_argument("--no-rebuild", action="store_true", help="不清空集合，直接追加")
    parser.add_argument("--chunk-size", type=int, default=200, help="切分块大小（字符数）")
    parser.add_argument("--chunk-overlap", type=int, default=30, help="相邻块重叠字符数")
    parser.add_argument("--include-long-docs", action="store_true", help="加入 long_documents 目录的长文档")
    args = parser.parse_args()
    build(
        rebuild=not args.no_rebuild,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        include_long_docs=args.include_long_docs,
    )


if __name__ == "__main__":
    main()
