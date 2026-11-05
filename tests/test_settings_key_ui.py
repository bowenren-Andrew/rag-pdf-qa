# -*- coding: utf-8 -*-
"""
设置页保存 Key 的界面级测试（BUG-04）

覆盖验收标准：
- 存 abc 被拒绝并提示；
- 存格式正确但无效的 Key 时明确失败，不显示“保存成功”，也不落库。
"""
import os

import httpx
from openai import AuthenticationError
from streamlit.testing.v1 import AppTest

import auth
import db
import qa

APP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
_REQUEST = httpx.Request("GET", "https://api.deepseek.com/models")


def _open_settings(tmp_db, username: str):
    user = db.create_user(username, auth.hash_password("Password123"))
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["user"] = {"id": user["id"], "username": username}
    at.run()
    page_hash = next(
        h for h, info in at._registered_pages.items() if info["page_name"] == "设置"
    )
    at._page_hash = page_hash
    at.run()
    return at, user


def _save_key(at, value: str):
    key_input = next(t for t in at.text_input if t.label == "填入新的 Key")
    key_input.set_value(value)
    next(b for b in at.button if b.label == "保存 Key").click().run()


def _messages(at):
    return [e.value for e in at.error] + [s.value for s in at.success]


def test_保存abc被拒绝且不落库(tmp_db):
    """abc 缺少 sk- 前缀，保存前就被拒绝，不能出现“保存成功”。"""
    at, user = _open_settings(tmp_db, "key_ui_format")

    _save_key(at, "abc")

    errors = [e.value for e in at.error]
    assert any("sk-" in msg for msg in errors)
    assert not any("保存成功" in msg or "已加密保存" in msg for msg in _messages(at))
    assert db.get_user_by_id(user["id"])["api_key_enc"] is None


def test_格式正确但无效的Key明确失败且不落库(tmp_db, monkeypatch):
    """格式合法但鉴权 401 时，必须提示无效且不保存，不能显示保存成功。"""

    class _FakeModels:
        def list(self):
            response = httpx.Response(
                401, request=_REQUEST, json={"error": {"message": "invalid key"}}
            )
            raise AuthenticationError("invalid", response=response, body=None)

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.models = _FakeModels()

    monkeypatch.setattr(qa, "OpenAI", _FakeOpenAI)
    at, user = _open_settings(tmp_db, "key_ui_invalid")

    _save_key(at, "sk-" + "a" * 32)

    errors = [e.value for e in at.error]
    assert any("无效或已过期" in msg for msg in errors)
    assert not any("已加密保存" in msg or "保存成功" in msg for msg in _messages(at))
    assert db.get_user_by_id(user["id"])["api_key_enc"] is None
