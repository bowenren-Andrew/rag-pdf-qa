# -*- coding: utf-8 -*-
"""注册页 UI 流程：邮箱验证码与 Turnstile 的双重门槛。"""
import os
import secrets

from streamlit.testing.v1 import AppTest

import auth
import db

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)


def _prepare(monkeypatch):
    """固定验证码、跳过真实邮件/Cloudflare 请求，并提供一个测试 site key。"""
    monkeypatch.setenv("APP_SECRET_KEY", secrets.token_urlsafe(32))
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "1x00000000000000000000AA")
    monkeypatch.setattr(
        auth.turnstile, "verify_token", lambda token, remote_ip=None: (True, "")
    )
    monkeypatch.setattr(auth, "_new_verification_code", lambda: "123456")
    monkeypatch.setattr(auth.mailer, "send_verification_code", lambda _e, _c: None)


def _open_register(monkeypatch):
    _prepare(monkeypatch)
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.run()
    at.radio[0].set_value("注册").run()
    return at


def _fill_register_form(at, username="register_flow", code=""):
    inputs = {t.label: t for t in at.text_input}
    inputs["设置用户名"].set_value(username)
    inputs["邮箱"].set_value(f"{username}@example.com")
    inputs["设置密码"].set_value("Password123")
    inputs["确认密码"].set_value("Password123")
    inputs["邮箱验证码"].set_value(code)
    return at


def _click(at, label):
    next(b for b in at.button if b.label == label).click().run()


def _set_turnstile_token(at, token):
    """AppTest 不会执行自定义组件 JS，直接把组件 widget 状态写成测试 token。"""
    elements = at.get("component_instance")
    if elements:
        at.session_state[elements[0].proto.id] = token
        at.session_state[elements[0].key] = token
    at.session_state["turnstile_token"] = token
    at.run()


def test_缺少邮箱验证码不能创建账号(monkeypatch, tmp_db):
    at = _open_register(monkeypatch)
    _set_turnstile_token(at, "test-token")
    _fill_register_form(at)

    _click(at, "注册")

    assert db.get_user_by_username("register_flow") is None
    assert any("验证码" in e.value for e in at.error)


def test_完整验证流程注册成功并切回登录页(monkeypatch, tmp_db):
    at = _open_register(monkeypatch)
    _set_turnstile_token(at, "test-token")
    _fill_register_form(at)

    _click(at, "发送验证码")
    assert any("验证码已发送" in s.value for s in at.success)

    _fill_register_form(at, code="123456")
    _click(at, "注册")

    assert at.radio[0].value == "登录"
    assert next(t for t in at.text_input if t.label == "用户名").value == "register_flow"
    assert any("注册成功" in s.value for s in at.success)
    assert db.get_user_by_username("register_flow") is not None


def test_没有Turnstile_token不能注册(monkeypatch, tmp_db):
    at = _open_register(monkeypatch)
    _fill_register_form(at, code="123456")
    elements = at.get("component_instance")
    if elements:
        at.session_state[elements[0].proto.id] = None
        at.session_state[elements[0].key] = None
    at.session_state["turnstile_token"] = None
    at.run()

    register_button = next(b for b in at.button if b.label == "注册")
    assert register_button.disabled is True
    assert db.get_user_by_username("register_flow") is None
