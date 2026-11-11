# -*- coding: utf-8 -*-
"""登录页邮箱验证码流程：Turnstile 门槛、自动建号与可跳过的首次引导。"""
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


def _open_email_login(monkeypatch):
    _prepare(monkeypatch)
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.run()
    assert at.radio[0].value == "邮箱验证码登录"
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


def _fill_email(at, email):
    next(t for t in at.text_input if t.label == "邮箱").set_value(email)


def _send_code(at, email):
    _fill_email(at, email)
    _click(at, "发送验证码")


def test_没有Turnstile_token不能发送验证码(monkeypatch, tmp_db):
    at = _open_email_login(monkeypatch)
    _fill_email(at, "no_turnstile@example.com")

    send_button = next(b for b in at.button if b.label == "发送验证码")
    assert send_button.disabled is True
    assert db.get_user_by_email("no_turnstile@example.com") is None
    assert at.session_state.get("user") is None


def test_完整验证流程自动建号并直接进入应用(monkeypatch, tmp_db):
    at = _open_email_login(monkeypatch)
    _set_turnstile_token(at, "test-token")
    _send_code(at, "new_flow@example.com")
    assert any("验证码已发送" in s.value for s in at.success)

    next(t for t in at.text_input if t.label == "邮箱验证码").set_value("123456")
    _click(at, "登录")

    created = db.get_user_by_email("new_flow@example.com")
    assert created is not None
    assert created["is_auto_created"] is True
    assert created["has_password"] is False
    assert created["email_verified_at"] is not None
    assert at.session_state["user"]["id"] == created["id"]
    assert any("今日剩余额度" in m.value for m in at.markdown)

    skip_label = "暂时跳过（保留临时用户名）"
    assert skip_label in [b.label for b in at.button]
    _click(at, skip_label)

    finished = db.get_user_by_id(created["id"])
    assert finished["username"] == created["username"]
    assert finished["onboarding_completed_at"] is not None
    assert finished["has_password"] is False


def test_验证码错误被中文提示拦回(monkeypatch, tmp_db):
    at = _open_email_login(monkeypatch)
    _set_turnstile_token(at, "test-token")
    _send_code(at, "wrong_code@example.com")

    next(t for t in at.text_input if t.label == "邮箱验证码").set_value("000000")
    _click(at, "登录")

    assert at.session_state.get("user") is None
    assert db.get_user_by_email("wrong_code@example.com") is None
    assert any("验证码不正确" in e.value for e in at.error)
