# -*- coding: utf-8 -*-
"""
vectorstore.py（v3）
职责：文本向量化与相似检索（单一职责，不涉及 PDF 解析和问答）。
- 使用 sentence-transformers 的 BAAI/bge-small-zh-v1.5 生成向量；
- v3 变更：Chroma 的 collection **按用户隔离**，命名为 pdf_chunks_u{user_id}，
  所有接口首个参数都是 user_id，从向量层保证用户之间看不到彼此的文档；
- v3 新增：delete_document(user_id, filename) 删除某用户指定文档的全部片段。
"""
import os

# 国内直连 HuggingFace 较慢，默认走 hf-mirror 镜像下载模型（约 100MB，仅首次需要）
# 必须在 import sentence_transformers 之前设置才生效
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import chromadb
from sentence_transformers import SentenceTransformer

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")

# BGE 中文模型官方建议：检索 query 前加指令前缀，文档侧不加
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

_model = None
_collections = {}  # {user_id: Chroma collection}，每个用户一个独立集合


def collection_name(user_id: int) -> str:
    """用户的向量集合名（v2 的全局 pdf_chunks 已废弃，旧数据不迁移）。"""
    return f"pdf_chunks_u{user_id}"


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


def _get_collection(user_id: int):
    """获取（或创建）某个用户的 Chroma 集合，数据持久化在 ./chroma_db。"""
    if user_id is None:
        raise ValueError("缺少 user_id，无法确定用户的向量集合。")
    global _collections
    if user_id not in _collections:
        client = chromadb.PersistentClient(path=DB_DIR)
        _collections[user_id] = client.get_or_create_collection(
            name=collection_name(user_id), metadata={"hnsw:space": "cosine"}
        )
    return _collections[user_id]


def document_exists(user_id: int, filename: str) -> bool:
    """检查某个 PDF 是否已入库（只看当前用户的数据），用于查重提示。"""
    result = _get_collection(user_id).get(where={"source": filename}, limit=1)
    return len(result["ids"]) > 0


def list_documents(user_id: int) -> list[str]:
    """列出当前用户知识库中已入库的所有文档名（去重、排序）。"""
    result = _get_collection(user_id).get()
    sources = {m.get("source", "") for m in result["metadatas"]}
    return sorted(s for s in sources if s)


def _doc_type(filename: str) -> str:
    """按文件名后缀判断文档类型（pdf / pptx），用于来源展示时区分「页」与「张」。"""
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    return "pptx" if ext == "pptx" else "pdf"


def add_document(user_id: int, filename: str, chunks: list[dict]) -> int:
    """
    把一个文档（PDF 或 PPTX）的文本片段向量化并写入当前用户的集合。
    :param chunks: loader.load_and_split_pdf / load_and_split_pptx 的返回结果
    :return: 实际写入的片段条数
    """
    if not chunks:
        return 0
    model = _get_model()
    texts = [c["text"] for c in chunks]
    embeddings = model.encode(texts, normalize_embeddings=True).tolist()
    ids = [f"{filename}::p{c['page']}::{i}" for i, c in enumerate(chunks)]
    doc_type = _doc_type(filename)
    metadatas = [
        {"source": filename, "page": c["page"], "doc_type": doc_type} for c in chunks
    ]
    _get_collection(user_id).add(
        ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas
    )
    return len(ids)


def similarity_search(
    user_id: int, query: str, top_k: int = 4, source: str = None
) -> list[dict]:
    """
    在当前用户的知识库内做相似检索。
    :param source: 可选，限定只在某个文档内检索（传文件名）
    :return: [{"text","source","page","doc_type","score"}]，score 为余弦距离，越小越相似
             （界面展示的相似度 = 1 - score；doc_type 为 pdf / pptx）
    """
    model = _get_model()
    query_vec = model.encode(
        QUERY_INSTRUCTION + query, normalize_embeddings=True
    ).tolist()
    kwargs = {"query_embeddings": [query_vec], "n_results": top_k}
    if source:
        kwargs["where"] = {"source": source}
    result = _get_collection(user_id).query(**kwargs)
    hits = []
    for doc, meta, dist in zip(
        result["documents"][0], result["metadatas"][0], result["distances"][0]
    ):
        hits.append(
            {
                "text": doc,
                "source": meta["source"],
                "page": meta["page"],
                # 旧数据可能没有该字段，默认按 pdf 展示
                "doc_type": meta.get("doc_type") or _doc_type(meta.get("source", "")),
                "score": dist,
            }
        )
    return hits


def delete_document(user_id: int, filename: str) -> int:
    """
    删除当前用户某个已入库文档的全部片段。
    :return: 实际删除的片段条数（0 表示文档不存在）
    """
    collection = _get_collection(user_id)
    result = collection.get(where={"source": filename})
    ids = result.get("ids", [])
    if not ids:
        return 0
    collection.delete(ids=ids)
    return len(ids)
