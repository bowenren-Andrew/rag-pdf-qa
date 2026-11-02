# -*- coding: utf-8 -*-
"""
vectorstore.py（v3.3）
职责：文本向量化与相似检索（单一职责，不涉及 PDF 解析和问答）。
- 使用 sentence-transformers 的 BAAI/bge-small-zh-v1.5 生成向量；
- v3 变更：知识库按用户隔离（db 层所有读写强制带 user_id）。
- v3.3 变更（持久化改造）：**Chroma 本地向量库退役**。片段与向量全部写入数据库
  （db.py 的 documents / document_chunks 表），从此云端重启 / 休眠后文档与检索能力不再丢失：
  * Postgres（Supabase）启用 pgvector 时：向量存 vector(512) 列，用 SQL 余弦距离检索；
  * sqlite（本地开发）或未启用 pgvector 时：向量存 float32 字节，
    取回后在 Python（numpy）里算余弦距离，结果与 SQL 路径一致。
- 检索范围支持三种限定：source（单文档）/ sources（多文档）/ session_id（会话绑定的文档）。
"""
import os

# 国内直连 HuggingFace 较慢，默认走 hf-mirror 镜像下载模型（约 100MB，仅首次需要）
# 必须在 import sentence_transformers 之前设置才生效
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import numpy as np
from sentence_transformers import SentenceTransformer

import db

MODEL_NAME = "BAAI/bge-small-zh-v1.5"

# BGE 中文模型官方建议：检索 query 前加指令前缀，文档侧不加
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

_model = None


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


def collection_name(user_id: int) -> str:
    """
    历史兼容函数（v3.2 及以前用于 Chroma 集合命名）。
    v3.3 起数据实际存数据库表 document_chunks，此函数仅保留给旧日志/提示使用。
    """
    return f"pdf_chunks_u{user_id}"


def _doc_type(filename: str) -> str:
    """按文件名后缀判断文档类型（pdf / pptx），用于来源展示时区分「页」与「张」。"""
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    return "pptx" if ext == "pptx" else "pdf"


# ---------------- 文档库管理 ----------------
def document_exists(user_id: int, filename: str) -> bool:
    """检查某个文档是否已入库（只看当前用户的数据），用于查重提示。"""
    return db.get_document_by_name(user_id, filename) is not None


def list_documents(user_id: int) -> list[str]:
    """列出当前用户知识库中已入库的所有文档名（按上传时间倒序）。"""
    return [d["filename"] for d in db.list_user_documents(user_id)]


def list_document_details(user_id: int) -> list[dict]:
    """列出当前用户全部文档的详细信息（含摘要、片段数、是否存了原始文件）。"""
    return db.list_user_documents(user_id)


def get_document_summary(user_id: int, filename: str) -> dict | None:
    """读取某个文档持久化的摘要（{"summary":..., "questions":[...]}），没有则 None。"""
    d = db.get_document_by_name(user_id, filename)
    return (d or {}).get("summary")


def save_document_summary(user_id: int, filename: str, brief: dict) -> bool:
    """把摘要与推荐问题持久化到文档上（随文档走，重启后仍在）。"""
    d = db.get_document_by_name(user_id, filename)
    if not d:
        return False
    return db.set_document_summary(user_id, d["id"], brief)


def add_document(
    user_id: int,
    filename: str,
    chunks: list[dict],
    session_id: int = None,
    file_data: bytes = None,
    file_size: int = 0,
) -> int:
    """
    把一个文档（PDF 或 PPTX）的文本片段向量化并写入数据库（持久化）。
    :param chunks: loader.load_and_split_pdf / load_and_split_pptx 的返回结果
    :param session_id: 可选，把文档同时绑定到该会话（会话检索默认只查绑定的文档）
    :param file_data: 可选，原始文件字节（存进数据库，供日后重新解析；过大可传 None）
    :param file_size: 原始文件字节数（用于展示）
    :return: 实际写入的片段条数（0 表示没有片段或文档已入库）
    """
    if not chunks:
        return 0
    if document_exists(user_id, filename):
        return 0  # 同名文档已存在，幂等跳过（界面层会先查重给出提示）

    model = _get_model()
    texts = [c["text"] for c in chunks]
    embeddings = model.encode(texts, normalize_embeddings=True)  # (n, 512) ndarray

    doc_id = db.create_document(
        user_id,
        filename,
        doc_type=_doc_type(filename),
        file_data=file_data,
        file_size=file_size,
    )
    rows = [
        {
            "page": c["page"],
            "text": c["text"],
            "embedding": np.asarray(embeddings[i], dtype=np.float32).tobytes(),
            "vector": embeddings[i].tolist(),
        }
        for i, c in enumerate(chunks)
    ]
    n = db.insert_chunks(user_id, doc_id, rows)
    if session_id:
        # 追加绑定（不动该会话已有的其他绑定）
        db.bind_documents_to_session(
            user_id, session_id, [doc_id], replace=False
        )
    return n


def delete_document(user_id: int, filename: str) -> int:
    """
    删除当前用户某个已入库文档的全部片段、摘要、原始文件与会话绑定。
    :return: 实际删除的片段条数（0 表示文档不存在）
    """
    d = db.get_document_by_name(user_id, filename)
    if not d:
        return 0
    return db.delete_document_record(user_id, d["id"])


# ---------------- 相似检索 ----------------
def _resolve_doc_ids(
    user_id: int, source: str, sources: list, session_id: int
) -> list[int]:
    """
    解析检索范围（优先级：source > sources > session_id > 全部文档）。
    会话未绑定任何文档时回退到「全部文档」，避免旧会话答不了新问题。
    """
    if source:
        return db.get_document_ids_by_names(user_id, [source])
    if sources:
        return db.get_document_ids_by_names(user_id, list(sources))
    if session_id:
        ids = db.get_session_document_ids(user_id, session_id)
        if ids:
            return ids
    return db.get_all_document_ids(user_id)


def similarity_search(
    user_id: int,
    query: str,
    top_k: int = 4,
    source: str = None,
    sources: list = None,
    session_id: int = None,
) -> list[dict]:
    """
    在指定范围内做相似检索（范围解析见 _resolve_doc_ids，总是限定在当前用户内）。
    :return: [{"text","source","page","doc_type","score"}]，score 为余弦距离，越小越相似
             （界面展示的相似度 = 1 - score；doc_type 为 pdf / pptx）
    """
    model = _get_model()
    query_vec = np.asarray(
        model.encode(QUERY_INSTRUCTION + query, normalize_embeddings=True)
    ).reshape(-1)  # 展平为一维（真实模型即一维，这里做防御）
    doc_ids = _resolve_doc_ids(user_id, source, sources, session_id)
    if not doc_ids:
        return []

    # 路径一：Postgres + pgvector，余弦距离直接在 SQL 里算
    if db.pgvector_enabled():
        rows = db.search_chunks_pgvector(
            user_id, doc_ids, query_vec.tolist(), top_k
        )
        hits = []
        for r in rows:
            fname = r["filename"]
            hits.append(
                {
                    "text": r["text"],
                    "source": fname,
                    "page": r["page"],
                    "doc_type": r["doc_type"] or _doc_type(fname),
                    "score": r["distance"],
                }
            )
        return hits

    # 路径二：通用回退（sqlite / 未启用 pgvector），取回片段后在 Python 里算余弦
    rows = db.fetch_chunks_for_similarity(user_id, doc_ids)
    if not rows:
        return []
    meta = db.get_document_meta(user_id, doc_ids)
    matrix = np.vstack(
        [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
    )
    # 入库时向量已归一化，这里再做一次防御性归一化，保证是严格的余弦相似度
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = matrix / norms
    q = np.asarray(query_vec, dtype=np.float32)
    q = q / max(float(np.linalg.norm(q)), 1e-12)
    sims = matrix @ q  # 余弦相似度（越大越相似）
    order = np.argsort(-sims)[:top_k]

    hits = []
    for idx in order:
        r = rows[int(idx)]
        m = meta.get(r["document_id"], {})
        fname = m.get("filename", "")
        hits.append(
            {
                "text": r["text"],
                "source": fname,
                "page": r["page"],
                "doc_type": m.get("doc_type") or _doc_type(fname),
                "score": float(1.0 - sims[int(idx)]),  # 距离 = 1 - 相似度
            }
        )
    return hits
