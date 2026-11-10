# -*- coding: utf-8 -*-
"""设置页只保留账号信息与修改密码，不再出现任何用户自填 Key 入口。"""
import os

from streamlit.testing.v1 import AppTest

import auth
import db

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)


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
    return at


def test_设置页不再显示任何Key入口(tmp_db, app_secret, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-platform-test-1234567890")
    at = _open_settings(tmp_db, "settings_no_key")

    assert "填入新的 Key" not in [t.label for t in at.text_input]
    button_labels = [b.label for b in at.button]
    assert "保存 Key" not in button_labels
    assert "删除已保存的 Key" not in button_labels

    body = " ".join(m.value for m in at.markdown) + " ".join(
        c.value for c in at.caption
    )
    assert "DeepSeek API Key" not in body
    assert "未设置（正在使用平台 Key）" not in body

    # 设置页核心功能仍在：账号信息与修改密码。
    assert "当前密码" in [t.label for t in at.text_input]
    assert "修改密码" in button_labels
