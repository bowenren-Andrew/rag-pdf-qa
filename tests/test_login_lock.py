# -*- coding: utf-8 -*-
"""登录限流（L-05）：连续失败 5 次按账号锁定 15 分钟，并提示剩余时间。"""
import os

import pytest
from streamlit.testing.v1 import AppTest

import auth
import db

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)


@pytest.fixture(autouse=True)
def _reset_login_lock():
    """每个用例前后清空进程内的失败计数，避免用例互相影响。"""
    auth._login_failures.clear()
    yield
    auth._login_failures.clear()


def test_连续失败4次不锁定第5次锁定15分钟():
    for _ in range(auth.LOGIN_MAX_FAILURES - 1):
        assert auth.record_login_failure("alice") == 0
        assert auth.get_login_lock_remaining("alice") == 0
    assert auth.record_login_failure("alice") == auth.LOGIN_LOCK_SECONDS
    assert auth.get_login_lock_remaining("alice") >= auth.LOGIN_LOCK_SECONDS - 1


def test_锁定按账号且大小写与空格不敏感():
    for _ in range(auth.LOGIN_MAX_FAILURES):
        auth.record_login_failure("  Alice ")
    assert auth.get_login_lock_remaining("alice") > 0
    assert auth.get_login_lock_remaining("ALICE") > 0
    assert auth.get_login_lock_remaining("bob") == 0


def test_成功登录后清零失败计数():
    for _ in range(auth.LOGIN_MAX_FAILURES - 1):
        auth.record_login_failure("carol")
    auth.clear_login_failures("carol")
    assert auth.get_login_lock_remaining("carol") == 0
    assert auth.record_login_failure("carol") == 0


def test_锁定到期后自动清除():
    for _ in range(auth.LOGIN_MAX_FAILURES):
        auth.record_login_failure("dave")
    # 模拟锁定时间已过（正数但早于当前 monotonic）
    auth._login_failures["dave"]["locked_until"] = 1.0
    assert auth.get_login_lock_remaining("dave") == 0
    assert "dave" not in auth._login_failures


def test_剩余时间提示格式():
    assert auth.format_lock_remaining(auth.LOGIN_LOCK_SECONDS) == "15 分 0 秒"
    assert auth.format_lock_remaining(65) == "1 分 5 秒"
    assert auth.format_lock_remaining(30) == "30 秒"


def _switch_to_password(at):
    at.radio[0].set_value("账号密码登录").run()


def _login(at, username, password):
    at.text_input[0].set_value(username)
    at.text_input[1].set_value(password)
    next(b for b in at.button if b.label == "登录").click().run()


def test_界面连续5次失败后锁定且正确密码也被拒绝(tmp_db):
    db.create_user("lock_ui", auth.hash_password("Password123"))
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.run()
    _switch_to_password(at)

    for _ in range(auth.LOGIN_MAX_FAILURES):
        _login(at, "lock_ui", "WrongPass123")

    assert any(
        "连续 5 次登录失败" in e.value and "15 分" in e.value for e in at.error
    )

    _login(at, "lock_ui", "Password123")

    assert at.session_state.get("user") is None
    assert any("临时锁定" in w.value and "后重试" in w.value for w in at.warning)


def test_失败未达阈值时正常登录不受影响(tmp_db):
    db.create_user("lock_ok", auth.hash_password("Password123"))
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.run()
    _switch_to_password(at)

    _login(at, "lock_ok", "WrongPass123")
    _login(at, "lock_ok", "WrongPass123")
    _login(at, "lock_ok", "Password123")

    assert at.session_state["user"]["username"] == "lock_ok"
