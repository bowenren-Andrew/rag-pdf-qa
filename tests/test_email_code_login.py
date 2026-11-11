# -*- coding: utf-8 -*-
"""邮箱验证码登录回归：自动建号、老账号、防刷限额与错误提示。"""
from datetime import datetime, timedelta
import re
import secrets

import pytest

import auth
import db


def _noop_send(_email, _code):
    return None


@pytest.fixture
def code_env(monkeypatch, tmp_db):
    monkeypatch.setenv("APP_SECRET_KEY", secrets.token_urlsafe(32))
    monkeypatch.setattr(
        auth.turnstile, "verify_token", lambda token, remote_ip=None: (True, "")
    )
    monkeypatch.setattr(auth, "_new_verification_code", lambda: "123456")
    return monkeypatch


def _send(email: str, client_ip: str | None = None) -> dict:
    result = auth.request_email_login_code(
        email,
        "test-token",
        client_ip=client_ip,
        sender=_noop_send,
    )
    assert result["ok"], result
    return result


def test_Turnstile未通过不发送验证码(code_env, tmp_db):
    code_env.setattr(
        auth.turnstile,
        "verify_token",
        lambda token, remote_ip=None: (False, "请完成人机验证"),
    )
    result = auth.request_email_login_code(
        "blocked@example.com", "", client_ip="203.0.113.10", sender=_noop_send
    )
    assert result["ok"] is False
    assert result["msg"] == "请完成人机验证"
    assert db.count_recent_email_codes(
        "blocked@example.com", datetime(2000, 1, 1)
    ) == 0


def test_新邮箱验证码登录自动创建无密码账号(code_env, tmp_db):
    _send("auto_create@example.com", client_ip="203.0.113.11")

    result = auth.login_with_email_code(
        "auto_create@example.com", "123456", client_ip="203.0.113.11"
    )

    assert result["ok"] is True
    assert result["created"] is True
    user = db.get_user_by_email("auto_create@example.com")
    assert user is not None
    assert re.fullmatch(r"user_[0-9a-f]{8}", user["username"])
    assert user["has_password"] is False
    assert user["email_verified_at"] is not None
    assert user["tier"] == "free"
    assert user["is_auto_created"] is True
    assert user["onboarding_completed_at"] is None


def test_老账号邮箱验证码直接登录(code_env, make_verified_user):
    existing = make_verified_user("email_login_old", email="old_login@example.com")
    _send("old_login@example.com")

    result = auth.login_with_email_code("old_login@example.com", "123456")

    assert result["ok"] is True
    assert result["created"] is False
    assert result["user"]["id"] == existing["id"]


def test_未验证老账号验证码登录后视为已验证(code_env, tmp_db):
    user = db.create_user(
        "was_unverified",
        auth.hash_password("Password123"),
        email="was_unverified@example.com",
        email_verified_at=None,
    )
    _send("was_unverified@example.com")

    result = auth.login_with_email_code("was_unverified@example.com", "123456")

    assert result["ok"] is True
    refreshed = db.get_user_by_id(user["id"])
    assert refreshed["email_verified_at"] is not None
    assert auth.login("was_unverified", "Password123") is not None


def test_验证码错误不能登录或建号(code_env, tmp_db):
    _send("wrong_code_login@example.com")

    result = auth.login_with_email_code(
        "wrong_code_login@example.com", "000000"
    )

    assert result["ok"] is False
    assert "验证码不正确" in result["msg"]
    assert db.get_user_by_email("wrong_code_login@example.com") is None


def test_验证码过期不能登录(code_env, tmp_db):
    base = datetime(2026, 11, 11, 8, 0, 0)
    code_env.setattr(auth, "_utcnow", lambda: base)
    _send("expired_login@example.com")

    code_env.setattr(auth, "_utcnow", lambda: base + timedelta(minutes=11))
    result = auth.login_with_email_code("expired_login@example.com", "123456")

    assert result["ok"] is False
    assert "已过期" in result["msg"]
    assert db.get_user_by_email("expired_login@example.com") is None


def test_同邮箱60秒内只能发一次登录码(code_env):
    _send("resend_limit@example.com")
    second = auth.request_email_login_code(
        "resend_limit@example.com", "test-token", sender=_noop_send
    )
    assert second["ok"] is False
    assert "1 分钟" in second["msg"]


def test_同邮箱每天最多5次登录码(code_env):
    clock = {"now": datetime(2026, 11, 11, 8, 0, 0)}

    def fake_now():
        value = clock["now"]
        clock["now"] += timedelta(seconds=61)
        return value

    code_env.setattr(auth, "_utcnow", fake_now)
    code_env.setattr(auth, "_beijing_today", lambda: "2026-11-11")
    for _ in range(5):
        _send("daily_login@example.com")

    blocked = auth.request_email_login_code(
        "daily_login@example.com", "test-token", sender=_noop_send
    )
    assert blocked["ok"] is False
    assert "上限（5 次）" in blocked["msg"]


def test_一次性邮箱不能请求登录码(code_env):
    result = auth.request_email_login_code(
        "x@mailinator.com", "test-token", sender=_noop_send
    )
    assert result["ok"] is False
    assert "一次性" in result["msg"] or "临时邮箱" in result["msg"]


def test_同一IP第4个自动建号被拦(code_env, tmp_db):
    ip = "198.51.100.210"
    for index in range(3):
        email = f"auto_ip_{index}@example.com"
        _send(email, client_ip=ip)
        result = auth.login_with_email_code(email, "123456", client_ip=ip)
        assert result["ok"] is True

    fourth_email = "auto_ip_3@example.com"
    _send(fourth_email, client_ip=ip)
    blocked = auth.login_with_email_code(fourth_email, "123456", client_ip=ip)
    assert blocked["ok"] is False
    assert blocked["msg"] == "今天该网络已注册满 3 个，明天再试"
    assert db.get_user_by_email(fourth_email) is None
