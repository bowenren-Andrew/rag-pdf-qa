# -*- coding: utf-8 -*-
"""
db.py（v3.3）
职责：数据库层（单一职责：只管表结构、连接与数据读写，不含任何界面逻辑）。
- 连接串从环境变量 DATABASE_URL 读取（支持 Streamlit Secrets 注入）；
  未配置时使用本地 sqlite 文件 app.db，方便零配置开发。
- v3：users（用户账号）与 sessions（聊天会话）两张表，结构保持不变。
- v3.3 新增三张表，解决「云端重启后文件与解析结果全丢」的持久化问题：
  documents         —— 文档库（归属用户；含摘要 JSON 与原始文件字节）
  document_chunks   —— 文档片段（文本 + 向量；Postgres 走 pgvector，sqlite 存 float32 字节）
  session_documents —— 会话与文档的绑定（多对多），加载旧会话时恢复其文档列表
- 所有读写都强制带 user_id，从数据层保证用户之间互不可见。
"""
import json
import os
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    bindparam,
    create_engine,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.types import UserDefinedType

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
# 本地默认数据库文件路径（已在 .gitignore 中忽略）
SQLITE_PATH = os.path.join(PROJECT_ROOT, "app.db")

Base = declarative_base()

# BAAI/bge-small-zh-v1.5 输出的向量维度（建 pgvector 列时用）
EMBEDDING_DIM = 512


def _resolve_database_url() -> str:
    """
    解析数据库连接串。
    优先级：环境变量 DATABASE_URL（云端在 Secrets 里配置）> 本地 sqlite 文件。
    Supabase 等平台给出的连接串可能是 postgres:// 前缀，SQLAlchemy 要求 postgresql://。
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        return url
    return f"sqlite:///{SQLITE_PATH}"


# 默认连接串（模块导入时计算一次），可用 set_database_url 切换（测试用）
DATABASE_URL = _resolve_database_url()

_engine = None


def get_engine():
    """获取（或创建）数据库引擎，懒加载，避免 import 时就连接数据库。"""
    global _engine
    if _engine is None:
        # pool_pre_ping：云端数据库可能断开连接，取连接前自动探活，避免使用失效连接
        kwargs = {"pool_pre_ping": True}
        if DATABASE_URL.startswith("sqlite"):
            # sqlite 多线程（Streamlit 每个会话一个线程）需要关闭同线程检查
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            # Postgres（Supabase）：免费版并发连接数很少，必须限制连接池规模，
            # 否则高峰期会出现 "remaining connection slots are reserved" 报错。
            # pool_recycle 定期回收长连接，防止被服务端（pooler）单方面断开。
            kwargs.update(
                pool_size=3,       # 常驻连接数
                max_overflow=5,    # 峰值最多再临时开 5 条（合计上限 8）
                pool_recycle=280,  # 秒；小于 Supabase pooler 的空闲超时
                pool_timeout=30,   # 等连接最多 30 秒，超时给出错误而不是一直卡住
            )
        _engine = create_engine(DATABASE_URL, **kwargs)
    return _engine


def set_database_url(url: str):
    """切换数据库连接串（主要用于测试指向临时 sqlite），会重置已有引擎。"""
    global DATABASE_URL, _engine, _pgvector_ok
    if _engine is not None:
        _engine.dispose()
        _engine = None
    _pgvector_ok = None  # 新库的 pgvector 状态需要重新探测
    DATABASE_URL = url


def init_db():
    """建表（已存在则跳过），应用启动时调用一次。Postgres 下还会尝试启用 pgvector。"""
    Base.metadata.create_all(get_engine())
    _ensure_pgvector()


# pgvector 是否可用的缓存结果：True / False / None（还没探测过）
_pgvector_ok = None


def _ensure_pgvector() -> bool:
    """
    在 Postgres 上启用 pgvector 扩展（幂等）。
    - 只有 Postgres 才尝试；sqlite（本地开发）直接返回 False；
    - 启用失败（无权限 / 托管平台未提供）也返回 False，上层自动回退到
      "float32 字节 + Python 余弦相似度" 的通用检索路径，功能不受影响。
    """
    global _pgvector_ok
    if _pgvector_ok is not None:
        return _pgvector_ok
    _pgvector_ok = False
    try:
        engine = get_engine()
        if engine.dialect.name != "postgresql":
            return False
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        _pgvector_ok = True
    except Exception:
        _pgvector_ok = False
    return _pgvector_ok


def pgvector_enabled() -> bool:
    """当前数据库是否启用了 pgvector（决定向量检索走 SQL 还是 Python 余弦）。"""
    return _ensure_pgvector()


# ---------------- 表定义 ----------------
class Vector(UserDefinedType):
    """
    pgvector 的 vector 类型（SQLAlchemy 自定义类型）。
    - 仅在 Postgres 上真正使用（写入/检索都走显式 ::vector 转换的 SQL）；
    - sqlite 的 DDL 也允许出现该类型名（sqlite 对未知类型宽容），该列恒为 NULL。
    """

    cache_ok = True

    def __init__(self, dim: int = EMBEDDING_DIM):
        super().__init__()
        self.dim = dim

    def get_col_spec(self, **kw):
        return f"vector({self.dim})"


class User(Base):
    """用户表。"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(32), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)  # pbkdf2_sha256$迭代次数$盐$哈希
    api_key_enc = Column(Text, nullable=True)  # Fernet 加密后的 DeepSeek Key
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class ChatSession(Base):
    """聊天会话表（归属某个用户）。"""

    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(100), nullable=False, default="新会话")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)
    messages = Column(Text, nullable=False, default="[]")  # JSON 文本


class Document(Base):
    """
    文档表（v3.3 新增）：用户上传的每一份 PDF / PPTX 一行。
    - 片段本体在 document_chunks；这里存文档级信息；
    - summary_json：全文摘要 + 3 个推荐问题（生成后写回，随文档持久化）；
    - file_data：原始文件字节（可选，超过大小上限时不存），供日后重新解析。
    """

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False, index=True)
    doc_type = Column(String(16), nullable=False, default="pdf")  # pdf / pptx
    chunk_count = Column(Integer, nullable=False, default=0)
    summary_json = Column(Text, nullable=True)  # {"summary":..., "questions":[...]}
    file_data = Column(LargeBinary, nullable=True)  # 原始文件字节（可能为空）
    file_size = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class DocumentChunk(Base):
    """
    文档片段表（v3.3 新增）：每条约 500 字的文本片段 + 向量。
    - embedding：float32 数组的字节（512 维 × 4 字节 = 2KB），sqlite 与通用回退路径使用；
    - embedding_vec：pgvector 的 vector(512) 列，仅 Postgres 启用扩展后写入/检索。
    两条路径并存：sqlite 只用前者，Postgres 优先用后者，检索结果完全一致。
    """

    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True)
    document_id = Column(
        Integer, ForeignKey("documents.id"), nullable=False, index=True
    )
    user_id = Column(Integer, nullable=False, index=True)  # 冗余存一份，检索免 join
    chunk_index = Column(Integer, nullable=False, default=0)
    page = Column(Integer, nullable=False, default=1)
    text = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=False)
    embedding_vec = Column(Vector(EMBEDDING_DIM), nullable=True)


class SessionDocument(Base):
    """
    会话-文档绑定表（v3.3 新增）：多对多。
    加载旧会话时按这张表恢复该会话的文档列表；问答检索默认限定在绑定的文档内。
    """

    __tablename__ = "session_documents"

    session_id = Column(
        Integer, ForeignKey("sessions.id"), primary_key=True
    )
    document_id = Column(
        Integer, ForeignKey("documents.id"), primary_key=True
    )
    created_at = Column(DateTime, nullable=False, default=datetime.now)


# ---------------- 通用：数据库会话上下文 ----------------
@contextmanager
def session_scope():
    """
    提供一个带事务的数据库会话。
    正常结束自动提交，异常自动回滚，无论如何都会关闭连接。
    """
    Session = sessionmaker(bind=get_engine(), expire_on_commit=False)
    db = Session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------- 用户读写 ----------------
def get_user_by_username(username: str) -> dict:
    """按用户名查用户，找到返回 dict，否则返回 None。"""
    with session_scope() as db:
        user = db.query(User).filter(User.username == username).one_or_none()
        return _user_to_dict(user) if user else None


def get_user_by_id(user_id: int) -> dict:
    """按 ID 查用户，找到返回 dict，否则返回 None。"""
    with session_scope() as db:
        user = db.query(User).filter(User.id == user_id).one_or_none()
        return _user_to_dict(user) if user else None


def create_user(username: str, password_hash: str) -> dict:
    """新建用户，返回创建后的用户信息（不含敏感字段以外的东西）。"""
    with session_scope() as db:
        user = User(username=username, password_hash=password_hash)
        db.add(user)
        db.flush()  # 先拿到自增 ID
        return _user_to_dict(user)


def update_password_hash(user_id: int, password_hash: str):
    """更新用户密码哈希。"""
    with session_scope() as db:
        db.query(User).filter(User.id == user_id).update(
            {"password_hash": password_hash}
        )


def update_api_key_enc(user_id: int, api_key_enc: str | None):
    """更新（或清空，传 None）用户的加密 API Key。"""
    with session_scope() as db:
        db.query(User).filter(User.id == user_id).update(
            {"api_key_enc": api_key_enc}
        )


def _user_to_dict(user: User) -> dict:
    """ORM 对象转普通 dict，避免会话关闭后再访问属性报错。"""
    return {
        "id": user.id,
        "username": user.username,
        "password_hash": user.password_hash,
        "api_key_enc": user.api_key_enc,
        "created_at": user.created_at,
    }


# ---------------- 会话读写（全部强制带 user_id） ----------------
def create_session(user_id: int, title: str = "新会话") -> int:
    """新建会话，返回会话 ID。"""
    with session_scope() as db:
        s = ChatSession(user_id=user_id, title=title, messages="[]")
        db.add(s)
        db.flush()
        return s.id


def list_sessions(user_id: int) -> list[dict]:
    """列出某用户的全部会话，按更新时间倒序。只看自己的数据。"""
    with session_scope() as db:
        rows = (
            db.query(ChatSession)
            .filter(ChatSession.user_id == user_id)
            .order_by(ChatSession.updated_at.desc())
            .all()
        )
        return [_session_to_dict(s) for s in rows]


def get_session(user_id: int, session_id: int) -> dict | None:
    """读取单个会话；同时校验归属，不属于该用户则返回 None（防越权）。"""
    with session_scope() as db:
        s = (
            db.query(ChatSession)
            .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .one_or_none()
        )
        return _session_to_dict(s) if s else None


def save_session(user_id: int, session_id: int, messages: list, title: str = None) -> bool:
    """
    保存会话消息（自动保存）。会话不存在或不属于该用户时返回 False。
    """
    with session_scope() as db:
        s = (
            db.query(ChatSession)
            .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .one_or_none()
        )
        if s is None:
            return False
        s.messages = json.dumps(messages, ensure_ascii=False)
        if title:
            s.title = title
        s.updated_at = datetime.now()
        return True


def delete_session(user_id: int, session_id: int) -> bool:
    """
    删除会话；只能删自己的，删别人的会失败返回 False。
    v3.3：顺带清理该会话的文档绑定（文档本身仍保留在用户的文档库里）。
    """
    with session_scope() as db:
        s = (
            db.query(ChatSession)
            .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .one_or_none()
        )
        if s is None:
            return False
        db.query(SessionDocument).filter(
            SessionDocument.session_id == session_id
        ).delete()
        db.delete(s)
        return True


def _session_to_dict(s: ChatSession) -> dict:
    """ORM 对象转 dict，并把 messages 的 JSON 文本解析回列表。"""
    try:
        messages = json.loads(s.messages)
    except Exception:
        messages = []  # 数据损坏时不让界面崩溃，按空会话处理
    return {
        "id": s.id,
        "user_id": s.user_id,
        "title": s.title,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "messages": messages,
    }


# ---------------- 文档读写（v3.3 新增，全部强制带 user_id） ----------------
def _document_to_dict(d: Document, with_summary: bool = True) -> dict:
    """ORM 对象转普通 dict（不读 file_data 大字段，避免无谓的内存开销）。"""
    out = {
        "id": d.id,
        "user_id": d.user_id,
        "filename": d.filename,
        "doc_type": d.doc_type,
        "chunk_count": d.chunk_count,
        "file_size": d.file_size,
        "has_file": d.file_data is not None,
        "created_at": d.created_at,
    }
    if with_summary:
        try:
            out["summary"] = json.loads(d.summary_json) if d.summary_json else None
        except Exception:
            out["summary"] = None  # JSON 损坏时按「无摘要」处理，不让界面崩溃
    return out


def create_document(
    user_id: int,
    filename: str,
    doc_type: str = "pdf",
    file_data: bytes | None = None,
    file_size: int = 0,
) -> int:
    """新建文档记录，返回文档 ID（片段随后用 insert_chunks 写入）。"""
    with session_scope() as db:
        d = Document(
            user_id=user_id,
            filename=filename,
            doc_type=doc_type,
            file_data=file_data,
            file_size=file_size,
        )
        db.add(d)
        db.flush()
        return d.id


def get_document(user_id: int, document_id: int) -> dict | None:
    """按 ID 取文档（校验归属）；不存在或不属于该用户返回 None。"""
    with session_scope() as db:
        d = (
            db.query(Document)
            .filter(Document.id == document_id, Document.user_id == user_id)
            .one_or_none()
        )
        return _document_to_dict(d) if d else None


def get_document_by_name(user_id: int, filename: str) -> dict | None:
    """按文件名取当前用户的文档（同名文件以最新一条为准）。"""
    with session_scope() as db:
        d = (
            db.query(Document)
            .filter(Document.user_id == user_id, Document.filename == filename)
            .order_by(Document.id.desc())
            .first()
        )
        return _document_to_dict(d) if d else None


def list_user_documents(user_id: int) -> list[dict]:
    """列出当前用户的全部文档（按上传时间倒序），不含 file_data。"""
    with session_scope() as db:
        rows = (
            db.query(Document)
            .filter(Document.user_id == user_id)
            .order_by(Document.id.desc())
            .all()
        )
        return [_document_to_dict(d) for d in rows]


def set_document_summary(user_id: int, document_id: int, brief: dict) -> bool:
    """把「全文摘要 + 推荐问题」持久化到文档上（JSON 文本）。"""
    with session_scope() as db:
        rows = (
            db.query(Document)
            .filter(Document.id == document_id, Document.user_id == user_id)
            .update({"summary_json": json.dumps(brief, ensure_ascii=False)})
        )
        return rows > 0


def get_document_file(user_id: int, document_id: int) -> bytes | None:
    """读取某个文档持久化的原始文件字节（没存或已删除返回 None）。"""
    with session_scope() as db:
        d = (
            db.query(Document)
            .filter(Document.id == document_id, Document.user_id == user_id)
            .one_or_none()
        )
        return d.file_data if d else None


def delete_document_record(user_id: int, document_id: int) -> int:
    """
    删除一个文档及其全部片段、摘要与会话绑定（只能删自己的）。
    :return: 删除的片段条数（0 表示文档不存在或不属于该用户）
    """
    with session_scope() as db:
        d = (
            db.query(Document)
            .filter(Document.id == document_id, Document.user_id == user_id)
            .one_or_none()
        )
        if d is None:
            return 0
        n_chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document_id)
            .count()
        )
        db.query(DocumentChunk).filter(
            DocumentChunk.document_id == document_id
        ).delete()
        db.query(SessionDocument).filter(
            SessionDocument.document_id == document_id
        ).delete()
        db.delete(d)
        return n_chunks


# ---------------- 片段写入与检索（v3.3 新增） ----------------
def insert_chunks(user_id: int, document_id: int, rows: list[dict]) -> int:
    """
    批量写入一个文档的片段与向量。
    :param rows: [{"page": 页码, "text": 文本, "embedding": float32字节,
                   "vector": [float×512] 或 None}]
    Postgres 启用 pgvector 时额外写入 embedding_vec（::vector 显式转换）；
    否则只写字节列。返回写入条数。
    """
    if not rows:
        return 0
    use_pg = pgvector_enabled()
    with get_engine().begin() as conn:
        if use_pg:
            sql = text(
                "INSERT INTO document_chunks "
                "(document_id, user_id, chunk_index, page, text, embedding, embedding_vec) "
                "VALUES (:document_id, :user_id, :chunk_index, :page, :text, :embedding, CAST(:vec AS vector))"
            )
            params = [
                {
                    "document_id": document_id,
                    "user_id": user_id,
                    "chunk_index": i,
                    "page": r["page"],
                    "text": r["text"],
                    "embedding": r["embedding"],
                    "vec": _vec_literal(r.get("vector")),
                }
                for i, r in enumerate(rows)
            ]
        else:
            sql = text(
                "INSERT INTO document_chunks "
                "(document_id, user_id, chunk_index, page, text, embedding, embedding_vec) "
                "VALUES (:document_id, :user_id, :chunk_index, :page, :text, :embedding, NULL)"
            )
            params = [
                {
                    "document_id": document_id,
                    "user_id": user_id,
                    "chunk_index": i,
                    "page": r["page"],
                    "text": r["text"],
                    "embedding": r["embedding"],
                }
                for i, r in enumerate(rows)
            ]
        conn.execute(sql, params)
    with session_scope() as db:
        db.query(Document).filter(Document.id == document_id).update(
            {"chunk_count": len(rows)}
        )
    return len(rows)


def _vec_literal(vec: list | None) -> str:
    """把浮点列表转成 pgvector 的字面量文本 '[0.1,0.2,...]'（None 返回 NULL 文本）。"""
    if not vec:
        return None
    return "[" + ",".join(f"{float(v):.6g}" for v in vec) + "]"


def search_chunks_pgvector(
    user_id: int, document_ids: list[int], query_vec: list[float], top_k: int
) -> list[dict]:
    """
    用 pgvector 做余弦距离检索（仅 Postgres + 已启用扩展时调用）。
    :return: [{"filename","doc_type","page","text","distance"}] 按距离升序
    """
    sql = text(
        "SELECT d.filename, d.doc_type, c.page, c.text, "
        "(c.embedding_vec <=> CAST(:q AS vector)) AS distance "
        "FROM document_chunks c JOIN documents d ON d.id = c.document_id "
        "WHERE c.user_id = :uid AND c.document_id IN :ids "
        "AND c.embedding_vec IS NOT NULL "
        "ORDER BY distance ASC LIMIT :k"
    ).bindparams(bindparam("ids", expanding=True))
    with get_engine().begin() as conn:
        rows = conn.execute(
            sql,
            {"q": _vec_literal(query_vec), "uid": user_id, "ids": list(document_ids), "k": top_k},
        ).mappings()
        return [
            {
                "filename": r["filename"],
                "doc_type": r["doc_type"],
                "page": r["page"],
                "text": r["text"],
                "distance": float(r["distance"]),
            }
            for r in rows
        ]


def fetch_chunks_for_similarity(
    user_id: int, document_ids: list[int]
) -> list[dict]:
    """
    通用回退路径：取回指定文档的全部片段（文本 + float32 向量字节），
    由调用方在 Python 里算余弦相似度。适用于 sqlite / 未启用 pgvector 的库。
    """
    sql = text(
        "SELECT c.document_id, c.page, c.text, c.embedding "
        "FROM document_chunks c "
        "WHERE c.user_id = :uid AND c.document_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    with get_engine().begin() as conn:
        rows = conn.execute(
            sql, {"uid": user_id, "ids": list(document_ids)}
        ).mappings()
        return [dict(r) for r in rows]


def get_document_ids_by_names(user_id: int, filenames: list[str]) -> list[int]:
    """把文件名列表解析成文档 ID 列表（只认当前用户的文档，找不到的忽略）。"""
    if not filenames:
        return []
    with session_scope() as db:
        rows = (
            db.query(Document.id)
            .filter(Document.user_id == user_id, Document.filename.in_(filenames))
            .all()
        )
        return [r[0] for r in rows]


def get_all_document_ids(user_id: int) -> list[int]:
    """当前用户全部文档的 ID（检索不限范围时用）。"""
    with session_scope() as db:
        rows = db.query(Document.id).filter(Document.user_id == user_id).all()
        return [r[0] for r in rows]


def get_document_meta(user_id: int, document_ids: list[int]) -> dict[int, dict]:
    """按 ID 批量取文档的文件名与类型（供回退检索路径组装结果用）。"""
    if not document_ids:
        return {}
    with session_scope() as db:
        rows = (
            db.query(Document)
            .filter(Document.user_id == user_id, Document.id.in_(document_ids))
            .all()
        )
        return {d.id: {"filename": d.filename, "doc_type": d.doc_type} for d in rows}


# ---------------- 会话-文档绑定（v3.3 新增） ----------------
def bind_documents_to_session(
    user_id: int, session_id: int, document_ids: list[int], replace: bool = True
) -> bool:
    """
    把文档绑定到某个会话（先校验会话与文档都属于当前用户，防越权绑定）。
    :param replace: True 时整体覆盖（先清掉旧绑定），False 时追加去重。
    """
    if not document_ids:
        if replace:
            with session_scope() as db:
                db.query(SessionDocument).filter(
                    SessionDocument.session_id == session_id
                ).delete()
            return True
        return True
    with session_scope() as db:
        s = (
            db.query(ChatSession)
            .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .one_or_none()
        )
        if s is None:
            return False
        owned = {
            r[0]
            for r in db.query(Document.id)
            .filter(Document.user_id == user_id, Document.id.in_(document_ids))
            .all()
        }
        if not owned:
            return False
        if replace:
            db.query(SessionDocument).filter(
                SessionDocument.session_id == session_id
            ).delete()
        existing = {
            r[0]
            for r in db.query(SessionDocument.document_id)
            .filter(SessionDocument.session_id == session_id)
            .all()
        }
        for doc_id in owned:
            if doc_id not in existing:
                db.add(
                    SessionDocument(session_id=session_id, document_id=doc_id)
                )
        return True


def unbind_document_from_session(
    user_id: int, session_id: int, document_id: int
) -> bool:
    """把单个文档从会话移除（校验会话归属）。"""
    with session_scope() as db:
        s = (
            db.query(ChatSession)
            .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .one_or_none()
        )
        if s is None:
            return False
        db.query(SessionDocument).filter(
            SessionDocument.session_id == session_id,
            SessionDocument.document_id == document_id,
        ).delete()
        return True


def get_session_document_ids(user_id: int, session_id: int) -> list[int]:
    """某个会话绑定的文档 ID 列表（校验会话归属，别人的会话返回空）。"""
    with session_scope() as db:
        s = (
            db.query(ChatSession)
            .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .one_or_none()
        )
        if s is None:
            return []
        rows = (
            db.query(SessionDocument.document_id)
            .filter(SessionDocument.session_id == session_id)
            .order_by(SessionDocument.created_at)
            .all()
        )
        return [r[0] for r in rows]


def list_session_documents(user_id: int, session_id: int) -> list[dict]:
    """某个会话绑定的文档列表（含摘要），加载旧会话时用它恢复文档与摘要。"""
    ids = get_session_document_ids(user_id, session_id)
    if not ids:
        return []
    with session_scope() as db:
        s = (
            db.query(ChatSession)
            .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .one_or_none()
        )
        if s is None:
            return []
        rows = (
            db.query(Document)
            .filter(Document.id.in_(ids))
            .order_by(Document.id)
            .all()
        )
        return [_document_to_dict(d) for d in rows]
