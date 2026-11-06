# -*- coding: utf-8 -*-
"""P3：提交后页面顶部「思考中」提示（点击回调 + 首个 delta 渲染）的测试。"""
import os
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import auth
import db
from ui_helpers import ask_banner_text, mark_ask_pending

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)


def test_思考中文案包含问题并截断():
    assert ask_banner_text("电池更换多少钱？") == "⏳ **思考中…** 正在准备回答：电池更换多少钱？"

    long_question = "问" * 40
    text = ask_banner_text(long_question)
    assert text.endswith("…")
    assert len(text) == len("⏳ **思考中…** 正在准备回答：") + 24 + 1


def test_思考中文案压缩空白():
    assert ask_banner_text("  电池   更换  ") == "⏳ **思考中…** 正在准备回答：电池 更换"


def test_空问题不记录待提问状态():
    state = {}
    mark_ask_pending(state, "   ")
    assert state == {}

    mark_ask_pending(state, None)
    assert state == {}


def test_记录待提问状态():
    state = {}
    mark_ask_pending(state, "  电池更换多少钱？ ")
    assert state["ask_pending"] == "电池更换多少钱？"


def _open_chat(tmp_db, username: str):
    user = db.create_user(username, auth.hash_password("Password123"))
    auth.set_user_api_key(user["id"], "sk-1234567890abcdefghij")
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["user"] = {"id": user["id"], "username": username}
    at.run()
    page_hash = next(
        h for h, info in at._registered_pages.items() if info["page_name"] == "问答"
    )
    at._page_hash = page_hash
    at.run()
    return at


def _ok_stream(*args, **kwargs):
    yield "这是回答"


def test_顶部思考中提示不会残留(tmp_db, fernet_key):
    """真实点击发送后，顶部占位必须清掉，不能和聊天区占位重复或残留。"""
    at = _open_chat(tmp_db, "p3_banner")
    at.text_area[0].set_value("电池更换多少钱？").run()

    with patch("qa.stream_answer", side_effect=_ok_stream), patch(
        "vectorstore.similarity_search", return_value=[]
    ):
        next(b for b in at.button if b.label == "发送").click().run()

    body = " ".join(m.value for m in at.markdown)
    assert "正在准备回答" not in body
    assert "思考中" not in body
    assert "这是回答" in body
    assert "ask_pending" not in at.session_state
