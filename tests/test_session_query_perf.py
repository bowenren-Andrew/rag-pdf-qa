# -*- coding: utf-8 -*-
"""
OBS-02/03 回归测试：会话加载/删除链路上的数据库查询。

以前 list_session_documents 一次要走 4 条查询，而且 Document 的 ORM 查询会把
原始文件字节（单个最大 20MB）整列读回来；文件一大，每次 rerun 都要重复传输，
表现为「加载会话 11.2s / 删除会话 14.3s」。

这里锁定两点：
1. 文档列表查询不读取原始文件字节，只取 has_file 布尔值；
2. list_session_documents 只发 1 条 SELECT（join 里同时校验归属）。
"""
from sqlalchemy import event

import db


def _capture_selects():
    """开始记录引擎上执行的 SQL，返回 (语句列表, 停止函数)。"""
    engine = db.get_engine()
    statements = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _listener)

    def _stop():
        event.remove(engine, "before_cursor_execute", _listener)
        return [s for s in statements if s.lstrip().upper().startswith("SELECT")]

    return statements, _stop


def test_文档列表查询不读取原始文件字节(tmp_db):
    """列表只取 has_file 布尔值，SELECT 里不能出现裸的 documents.file_data 列。"""
    uid = db.create_user("perf_a", "hash")["id"]
    db.create_document(uid, "big.pdf", file_data=b"x" * 1024, file_size=1024)

    _, stop = _capture_selects()
    try:
        rows = db.list_user_documents(uid)
    finally:
        selects = stop()

    assert rows and rows[0]["has_file"] is True
    assert selects, "应当至少执行一条 SELECT"
    assert any("IS NOT NULL" in s for s in selects)
    assert not any("file_data," in s for s in selects), "SELECT 不应整列读取 file_data"


def test_会话文档列表只发一条查询(tmp_db):
    """加载会话时恢复文档列表只允许 1 条 SELECT（join + 归属校验）。"""
    uid = db.create_user("perf_b", "hash")["id"]
    sid = db.create_session(uid, "会话")
    d1 = db.create_document(uid, "a.pdf")
    d2 = db.create_document(uid, "b.pdf")
    db.bind_documents_to_session(uid, sid, [d1, d2])

    _, stop = _capture_selects()
    try:
        docs = db.list_session_documents(uid, sid)
    finally:
        selects = stop()

    assert [d["filename"] for d in docs] == ["a.pdf", "b.pdf"]
    assert len(selects) == 1, f"应只发 1 条 SELECT，实际 {len(selects)} 条"


def test_会话文档列表校验归属(tmp_db):
    """别人的会话拿不到文档。"""
    owner = db.create_user("perf_c", "hash")["id"]
    other = db.create_user("perf_d", "hash")["id"]
    sid = db.create_session(owner, "会话")
    doc_id = db.create_document(owner, "a.pdf")
    db.bind_documents_to_session(owner, sid, [doc_id])

    assert db.list_session_documents(other, sid) == []
