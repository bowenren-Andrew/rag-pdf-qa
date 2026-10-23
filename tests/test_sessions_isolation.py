# -*- coding: utf-8 -*-
"""
会话数据隔离的单元测试（重点：防止越权访问别人的会话）

测试原则：
1. 用临时 sqlite 建两个用户，各存一个会话；
2. 断言按 user_id 查询互不可见，跨用户删除/保存必须失败；
3. 全程不连真实数据库、不加载向量模型、不调用 DeepSeek API。
"""
import auth
import db

_USER_A = {"username": "user_a", "password": "PassA123456"}
_USER_B = {"username": "user_b", "password": "PassB123456"}


def _make_two_users_with_one_session_each():
    """造数据：两个用户，各自一个会话，返回 (用户A信息, 用户B信息)。"""
    user_a = auth.register(_USER_A["username"], _USER_A["password"])["user"]
    user_b = auth.register(_USER_B["username"], _USER_B["password"])["user"]

    session_a = db.create_session(user_a["id"], title="A 的会话")
    session_b = db.create_session(user_b["id"], title="B 的会话")
    db.save_session(user_a["id"], session_a, [{"role": "user", "content": "A 的提问"}])
    db.save_session(user_b["id"], session_b, [{"role": "user", "content": "B 的提问"}])
    return {"user": user_a, "session_id": session_a}, {
        "user": user_b,
        "session_id": session_b,
    }


def test_每个用户只能看到自己的会话(tmp_db):
    """list_sessions 按 user_id 过滤，看不到别人的会话。"""
    a, b = _make_two_users_with_one_session_each()

    sessions_a = db.list_sessions(a["user"]["id"])
    sessions_b = db.list_sessions(b["user"]["id"])

    assert len(sessions_a) == 1 and len(sessions_b) == 1
    assert sessions_a[0]["id"] == a["session_id"]  # A 只能看到自己的那条
    assert sessions_b[0]["id"] == b["session_id"]
    assert sessions_a[0]["id"] != sessions_b[0]["id"]


def test_读取别人的会话返回None(tmp_db):
    """即使知道别人的会话 ID，get_session 也必须返回 None（防越权读取）。"""
    a, b = _make_two_users_with_one_session_each()

    assert db.get_session(a["user"]["id"], a["session_id"]) is not None      # 自己的能读
    assert db.get_session(a["user"]["id"], b["session_id"]) is None          # 别人的读不到


def test_保存别人的会话会失败(tmp_db):
    """用别人的会话 ID 调 save_session 必须返回 False，不能改写他人数据。"""
    a, b = _make_two_users_with_one_session_each()

    ok = db.save_session(a["user"]["id"], b["session_id"], [{"role": "user", "content": "越权写入"}])
    assert ok is False

    untouched = db.get_session(b["user"]["id"], b["session_id"])
    assert untouched["messages"][0]["content"] == "B 的提问"  # 内容没被改动


def test_删除别人的会话删不掉(tmp_db):
    """delete_session 带 user_id 条件，删别人会话应返回 False 且数据仍在。"""
    a, b = _make_two_users_with_one_session_each()

    deleted = db.delete_session(a["user"]["id"], b["session_id"])
    assert deleted is False
    assert db.get_session(b["user"]["id"], b["session_id"]) is not None


def test_删除自己的会话可以成功(tmp_db):
    """正常路径：删自己的会话返回 True，之后再查为空。"""
    a, b = _make_two_users_with_one_session_each()

    assert db.delete_session(a["user"]["id"], a["session_id"]) is True
    assert db.get_session(a["user"]["id"], a["session_id"]) is None
    assert len(db.list_sessions(a["user"]["id"])) == 0
    assert len(db.list_sessions(b["user"]["id"])) == 1  # B 的数据不受影响


def test_会话消息读写往返正常(tmp_db):
    """自己的会话保存后能完整读回（含中文与 sources 字段）。"""
    a, _ = _make_two_users_with_one_session_each()
    messages = [
        {"role": "user", "content": "什么是 ACL？"},
        {"role": "assistant", "content": "ACL 分为基本 ACL 与高级 ACL。", "sources": [{"source": "demo.pdf", "page": 3, "score": 0.2, "text": "片段"}]},
    ]

    db.save_session(a["user"]["id"], a["session_id"], messages, title="中文标题")
    loaded = db.get_session(a["user"]["id"], a["session_id"])

    assert loaded["title"] == "中文标题"
    assert loaded["messages"][0]["content"] == "什么是 ACL？"
    assert loaded["messages"][1]["sources"][0]["source"] == "demo.pdf"
