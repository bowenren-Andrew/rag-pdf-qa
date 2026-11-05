# -*- coding: utf-8 -*-
"""注册成功后自动切回登录页并回填用户名（BUG-09）。"""
import os

from streamlit.testing.v1 import AppTest

import db

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)


def test_注册成功自动切到登录页并回填用户名(tmp_db):
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.run()

    at.radio[0].set_value("注册").run()
    at.text_input[0].set_value("register_flow")
    at.text_input[1].set_value("Password123")
    at.text_input[2].set_value("Password123")
    next(b for b in at.button if b.label == "注册").click().run()

    assert at.radio[0].value == "登录"
    assert next(t for t in at.text_input if t.label == "用户名").value == "register_flow"
    assert any("注册成功" in s.value for s in at.success)
    assert db.get_user_by_username("register_flow") is not None
