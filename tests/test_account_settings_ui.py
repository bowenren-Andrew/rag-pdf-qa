# -*- coding: utf-8 -*-
"""设置页 UI：当前用户名、修改用户名、无密码账号的设置密码入口。"""
from datetime import datetime
import os

from streamlit.testing.v1 import AppTest

import auth
import db

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)


def _auto_user(username: str = "user_settings") -> dict:
    return db.create_user(
        username,
        None,
        email=f"{username}@example.com",
        email_verified_at=datetime.now(),
        is_auto_created=True,
        onboarding_completed_at=datetime.now(),
    )


def _open_settings(user: dict) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["user"] = {"id": user["id"], "username": user["username"]}
    at.run()
    page_hash = next(
        h for h, info in at._registered_pages.items() if info["page_name"] == "设置"
    )
    at._page_hash = page_hash
    at.run()
    return at


def test_无密码账号设置页显示入口并能设置密码(tmp_db):
    user = _auto_user()
    at = _open_settings(user)

    assert "当前用户名" in [t.label for t in at.text_input]
    assert "设置密码" in [b.label for b in at.button]

    inputs = {t.label: t for t in at.text_input}
    inputs["新密码"].set_value("SettingsPass123")
    inputs["确认新密码"].set_value("SettingsPass123")
    next(b for b in at.button if b.label == "设置密码").click().run()

    refreshed = db.get_user_by_id(user["id"])
    assert refreshed["has_password"] is True
    assert auth.login(user["username"], "SettingsPass123") is not None


def test_设置页可以修改用户名(tmp_db):
    user = _auto_user("user_rename")
    at = _open_settings(user)

    next(t for t in at.text_input if t.label == "新用户名").set_value("renamed_user")
    next(b for b in at.button if b.label == "更新用户名").click().run()

    assert db.get_user_by_id(user["id"])["username"] == "renamed_user"
    assert at.session_state["user"]["username"] == "renamed_user"
