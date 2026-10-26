# -*- coding: utf-8 -*-
"""
db.py（v3）
职责：数据库层（单一职责：只管表结构、连接与数据读写，不含任何界面逻辑）。
- 连接串从环境变量 DATABASE_URL 读取（支持 Streamlit Secrets 注入）；
  未配置时使用本地 sqlite 文件 app.db，方便零配置开发。
- 两张表：
  users    —— 用户账号（用户名唯一、密码哈希、加密后的 API Key）
  sessions —— 聊天会话（归属某个用户，messages 以 JSON 文本存储）
- 所有会话读写都强制带 user_id，从数据层保证用户之间互不可见。
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
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
# 本地默认数据库文件路径（已在 .gitignore 中忽略）
SQLITE_PATH = os.path.join(PROJECT_ROOT, "app.db")

Base = declarative_base()


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
    global DATABASE_URL, _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
    DATABASE_URL = url


def init_db():
    """建表（已存在则跳过），应用启动时调用一次。"""
    Base.metadata.create_all(get_engine())


# ---------------- 表定义 ----------------
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
    """
    with session_scope() as db:
        rows = (
            db.query(ChatSession)
            .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .delete()
        )
        return rows > 0


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
