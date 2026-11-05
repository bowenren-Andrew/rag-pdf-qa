# -*- coding: utf-8 -*-
"""
删除确认弹窗的界面级测试（BUG-03）

用 Streamlit 官方 AppTest 驱动真实 app.py：
- 点击「删除」只弹确认框，数据不动；
- 点「取消」后数据仍在；
- 点「确认删除」后才真正删除。
"""
import os

import pytest
from streamlit.testing.v1 import AppTest

import auth
import db

APP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _new_app(tmp_db, username: str):
    """建一个测试用户并启动 AppTest，返回 (AppTest 实例, 用户 dict)。"""
    user = db.create_user(username, auth.hash_password("Password123"))
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["user"] = {"id": user["id"], "username": username}
    at.run()
    return at, user


def _switch_page(at, page_name: str):
    """st.navigation 的 callable 页面没有文件路径，用注册表里的 hash 切换。"""
    page_hash = next(
        h for h, info in at._registered_pages.items() if info["page_name"] == page_name
    )
    at._page_hash = page_hash
    at.run()
    return at


def _click(at, label: str):
    matches = [b for b in at.button if b.label == label]
    assert matches, f"页面上找不到按钮：{label}"
    return matches[0].click().run()


def _labels(at):
    return [b.label for b in at.button]


def test_删除文档先弹确认且取消后保留(tmp_db):
    """点「删除」不应立即删数据；确认框里必须写明文档名与不可恢复。"""
    at, user = _new_app(tmp_db, "del_doc_cancel")
    db.create_document(user["id"], "qa-report.pdf")
    _switch_page(at, "我的文档")

    _click(at, "删除")

    assert "确认删除" in _labels(at) and "取消" in _labels(at)
    assert db.get_document_by_name(user["id"], "qa-report.pdf") is not None
    body = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "qa-report.pdf" in body
    assert "无法恢复" in body

    _click(at, "取消")

    assert "确认删除" not in _labels(at)
    assert db.get_document_by_name(user["id"], "qa-report.pdf") is not None


def test_确认后删除文档(tmp_db):
    """点「确认删除」后才真正删除，并提示已删除。"""
    at, user = _new_app(tmp_db, "del_doc_confirm")
    db.create_document(user["id"], "qa-report.pdf")
    _switch_page(at, "我的文档")

    _click(at, "删除")
    _click(at, "确认删除")

    assert db.get_document_by_name(user["id"], "qa-report.pdf") is None
    assert [s.value for s in at.success], "删除后应给出成功提示"


def test_删除会话先弹确认且取消后保留(tmp_db):
    """会话删除同样需要确认，取消后会话记录仍在。"""
    at, user = _new_app(tmp_db, "del_sess_cancel")
    session_id = db.create_session(user["id"], "待删会话")
    at.run()  # 重新渲染侧栏，让新会话和删除按钮出现

    _click(at, "删除")

    assert "确认删除" in _labels(at) and "取消" in _labels(at)
    assert db.get_session(user["id"], session_id) is not None
    body = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "待删会话" in body
    assert "无法恢复" in body

    _click(at, "取消")

    assert db.get_session(user["id"], session_id) is not None


def test_确认后删除会话(tmp_db):
    """确认后才删除会话记录。"""
    at, user = _new_app(tmp_db, "del_sess_confirm")
    session_id = db.create_session(user["id"], "待删会话")
    at.run()

    _click(at, "删除")
    _click(at, "确认删除")

    assert db.get_session(user["id"], session_id) is None
