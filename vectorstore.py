# -*- coding: utf-8 -*-
"""
vectorstore.py（v2）
职责：文本向量化与相似检索（单一职责，不涉及 PDF 解析和问答）。
- 使用 sentence-transformers 的 BAAI/bge-small-zh-v1.5 生成向量；
- 向量写入本地 Chroma（PersistentClient，目录 ./chroma_db），重启后可复用；
- v2 新增：similarity_search 支持按来源文档过滤、list_documents 列出已入库文档。
"""
import os

# 国内直连 HuggingFace 较慢，默认走 hf-mirror 镜像下载模型（约 100MB，仅首次需要）
# 必须在 import sentence_transformers 之前设置才生效
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import chromadb
from sentence_transformers import SentenceTransformer

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
COLLECTION_NAME = "pdf_chunks"

# BGE 中文模型官方建议：检索 query 前加指令前缀，文档侧不加
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

_model = None
_collection = None


def _get_model() -> SentenceTransformer:
    """懒加载向量模型，避免 import 时就触发下载/加载。"""
    global _model
    if _model is None:
        try:
            _model = SentenceTransformer(MODEL_NAME)
        except Exception as e:
            raise RuntimeError(
                f"向量模型 {MODEL_NAME} 加载失败，请检查网络后重试。原始错误：{e}"
            ) from e
    return _model


def _get_collection():
    """获取（或创建）Chroma 集合，数据持久化在本文件同级的 ./chroma_db 目录。"""
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=DB_DIR)
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
    return _collection


def document_exists(filename: str) -> bool:
    """检查某个 PDF 是否已入过库，避免重启后重复向量化。"""
    result = _get_collection().get(where={"source": filename}, limit=1)
    return len(result["ids"]) > 0


def list_documents() -> list[str]:
    """列出知识库中已入库的所有文档名（去重、排序）。"""
    result = _get_collection().get()
    sources = {m.get("source", "") for m in result["metadatas"]}
    return sorted(s for s in sources if s)


def add_document(filename: str, chunks: list[dict]) -> int:
    """
    把一个 PDF 的文本片段向量化并写入 Chroma。
    :param filename: PDF 文件名（作为来源标识）
    :param chunks: loader.load_and_split_pdf 的返回结果
    :return: 实际写入的片段条数
    """
    if not chunks:
        return 0
    model = _get_model()
    texts = [c["text"] for c in chunks]
    embeddings = model.encode(texts, normalize_embeddings=True).tolist()
    ids = [f"{filename}::p{c['page']}::{i}" for i, c in enumerate(chunks)]
    metadatas = [{"source": filename, "page": c["page"]} for c in chunks]
    _get_collection().add(
        ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas
    )
    return len(ids)


def similarity_search(query: str, top_k: int = 4, source: str = None) -> list[dict]:
    """
    相似检索。
    :param source: 可选，限定只在某个文档内检索（传文件名）
    :return: [{"text","source","page","score"}]，score 为余弦距离，越小越相似
             （界面展示的相似度 = 1 - score）
    """
    model = _get_model()
    query_vec = model.encode(
        QUERY_INSTRUCTION + query, normalize_embeddings=True
    ).tolist()
    kwargs = {"query_embeddings": [query_vec], "n_results": top_k}
    if source:
        kwargs["where"] = {"source": source}
    result = _get_collection().query(**kwargs)
    hits = []
    for doc, meta, dist in zip(
        result["documents"][0], result["metadatas"][0], result["distances"][0]
    ):
        hits.append(
            {"text": doc, "source": meta["source"], "page": meta["page"], "score": dist}
        )
    return hits
