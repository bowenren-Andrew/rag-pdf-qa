# -*- coding: utf-8 -*-
"""提问输入上限（BUG-08）的界面级测试。"""
import os

from streamlit.testing.v1 import AppTest

import auth
import db

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)


def _open_chat(tmp_db, username: str):
    user = db.create_user(username, auth.hash_password("Password123"))
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["user"] = {"id": user["id"], "username": username}
    at.run()
    page_hash = next(
        h for h, info in at._registered_pages.items() if info["page_name"] == "问答"
    )
    at._page_hash = page_hash
    at.run()
    return at


def test_输入框显示已输入与上限字数(tmp_db):
    """输入框下方必须实时显示「已输入 / 上限」。"""
    at = _open_chat(tmp_db, "question_limit")

    assert at.text_area[0].label == "请输入你的问题…"
    assert any("已输入 0 / 2000 字" in c.value for c in at.caption)


def test_输入内容后字数统计同步更新(tmp_db):
    """输入后计数随内容长度变化。"""
    at = _open_chat(tmp_db, "question_counter")

    at.text_area[0].set_value("x" * 123).run()

    assert any("已输入 123 / 2000 字" in c.value for c in at.caption)


def test_超过2000字被提示并禁止提交(tmp_db):
    """粘贴超长内容必须给出明确提示，并禁用发送按钮。"""
    at = _open_chat(tmp_db, "question_guard")

    at.text_area[0].set_value("x" * 2001).run()

    assert len(at.text_area[0].value or "") == 2001
    assert any("超过上限 2000 字" in w.value for w in at.warning)
    assert next(b for b in at.button if b.label == "发送").proto.disabled is True
    assert not any("还没有填写 DeepSeek API Key" in w.value for w in at.warning)


def test_2000字以内可进入提交流程(tmp_db):
    """恰好 2000 字应通过长度校验（没有 Key 时由既有 Key 引导拦截）。"""
    at = _open_chat(tmp_db, "question_ok")

    at.text_area[0].set_value("x" * 2000).run()
    next(b for b in at.button if b.label == "发送").click().run()

    assert not any("不能超过 2000 字" in w.value for w in at.warning)
    assert any("还没有填写 DeepSeek API Key" in w.value for w in at.warning)
