# -*- coding: utf-8 -*-
"""v3.9 防刷注册：邮箱验证码、Turnstile、同 IP 注册与额度双限。"""
from datetime import datetime, timedelta
import secrets

import pytest

import auth
import db
import quota
import request_ip


def _noop_send(_email, _code):
    return None


@pytest.fixture
def anti_abuse_env(monkeypatch, tmp_db):
    """固定验证码、跳过真实邮件/Turnstile，并配置验证码哈希所需的密钥。"""
    monkeypatch.setenv("APP_SECRET_KEY", secrets.token_urlsafe(32))
    monkeypatch.setattr(
        auth.turnstile, "verify_token", lambda token, remote_ip=None: (True, "")
    )
    monkeypatch.setattr(auth, "_new_verification_code", lambda: "123456")
    return monkeypatch


@pytest.mark.parametrize(
    "email, ok",
    [
        ("student@example.com", True),
        ("a.b+tag@sub.example.edu.cn", True),
        ("", False),
        ("not-an-email", False),
        ("a@b", False),
        ("a@example..com", False),
    ],
)
def test_邮箱格式校验(email, ok):
    assert auth.validate_email(email)[0] is ok


@pytest.mark.parametrize(
    "email",
    [
        "x@mailinator.com",
        "x@sub.mailinator.com",
        "x@10minutemail.com",
        "x@yopmail.com",
    ],
)
def test_拒绝常见临时邮箱(email):
    ok, msg = auth.validate_email(email)
    assert ok is False
    assert "一次性" in msg or "临时邮箱" in msg


def test_验证码同邮箱1分钟只能发一次(anti_abuse_env):
    first = auth.request_email_code("rate@example.com", sender=_noop_send)
    second = auth.request_email_code("rate@example.com", sender=_noop_send)
    assert first["ok"] is True
    assert second["ok"] is False
    assert "1 分钟" in second["msg"]


def test_验证码同邮箱一天最多5次(anti_abuse_env):
    clock = {"now": datetime(2026, 11, 10, 8, 0, 0)}

    def fake_now():
        clock["now"] += timedelta(seconds=61)
        return clock["now"]

    anti_abuse_env.setattr(auth, "_utcnow", fake_now)
    anti_abuse_env.setattr(auth, "_beijing_today", lambda: "2026-11-10")
    for _ in range(5):
        assert auth.request_email_code("daily@example.com", sender=_noop_send)["ok"]

    result = auth.request_email_code("daily@example.com", sender=_noop_send)
    assert result["ok"] is False
    assert "上限（5 次）" in result["msg"]


def test_验证码过期不能注册(anti_abuse_env):
    base = datetime(2026, 11, 10, 8, 0, 0)
    anti_abuse_env.setattr(auth, "_utcnow", lambda: base)
    assert auth.request_email_code("expired@example.com", sender=_noop_send)["ok"]

    anti_abuse_env.setattr(auth, "_utcnow", lambda: base + timedelta(minutes=11))
    result = auth.register(
        "expired_user",
        "Password123",
        email="expired@example.com",
        verification_code="123456",
        turnstile_token="token",
        client_ip="198.51.100.1",
    )
    assert result["ok"] is False
    assert "已过期" in result["msg"]


def test_验证码输错不能注册且累计次数(anti_abuse_env):
    assert auth.request_email_code("wrong@example.com", sender=_noop_send)["ok"]
    result = None
    for _ in range(5):
        result = auth.register(
            "wrong_user",
            "Password123",
            email="wrong@example.com",
            verification_code="000000",
            turnstile_token="token",
            client_ip="198.51.100.2",
        )
        assert result["ok"] is False
    assert "验证码不正确" in result["msg"]

    locked = auth.register(
        "wrong_user",
        "Password123",
        email="wrong@example.com",
        verification_code="000000",
        turnstile_token="token",
        client_ip="198.51.100.2",
    )
    assert "错误次数过多" in locked["msg"]
    assert db.get_user_by_username("wrong_user") is None


def test_Turnstile失败只给中文提示(anti_abuse_env):
    anti_abuse_env.setattr(
        auth.turnstile,
        "verify_token",
        lambda token, remote_ip=None: (False, "请完成人机验证"),
    )
    result = auth.register(
        "bot_user",
        "Password123",
        email="bot@example.com",
        verification_code="123456",
        turnstile_token="",
        client_ip="198.51.100.3",
    )
    assert result["ok"] is False
    assert result["msg"] == "请完成人机验证"


def test_注册成功写入邮箱验证时间与IP键(register_verified):
    result = register_verified(
        "verified_user",
        "Password123",
        email="Mixed@Example.com",
        client_ip="198.51.100.4",
    )
    assert result["ok"] is True
    user = result["user"]
    assert user["email"] == "mixed@example.com"
    assert user["email_verified_at"] is not None
    assert user["registration_ip_hash"] == request_ip.hash_ip("198.51.100.4")


def test_同一IP第4个注册被拦(anti_abuse_env):
    ip = "198.51.100.77"
    for index in range(3):
        email = f"sameip{index}@example.com"
        assert auth.request_email_code(email, sender=_noop_send)["ok"]
        result = auth.register(
            f"sameip{index}",
            "Password123",
            email=email,
            verification_code="123456",
            turnstile_token="token",
            client_ip=ip,
        )
        assert result["ok"] is True

    email = "sameip3@example.com"
    assert auth.request_email_code(email, sender=_noop_send)["ok"]
    blocked = auth.register(
        "sameip3",
        "Password123",
        email=email,
        verification_code="123456",
        turnstile_token="token",
        client_ip=ip,
    )
    assert blocked["ok"] is False
    assert blocked["msg"] == "今天该网络已注册满 3 个，明天再试"


def test_未验证账号不能登录(tmp_db):
    db.create_user(
        "unverified",
        auth.hash_password("Password123"),
        email="unverified@example.com",
        email_verified_at=None,
    )
    with pytest.raises(auth.AuthFriendlyError, match="邮箱验证"):
        auth.login("unverified", "Password123")


def test_历史无邮箱账号迁移后仍可登录(tmp_db):
    db.create_user(
        "legacy_user",
        auth.hash_password("Password123"),
        email=None,
        email_verified_at=None,
    )
    db._migrate_schema()
    assert auth.login("legacy_user", "Password123") is not None


def _three_free_users_same_ip(make_verified_user, ip_hash):
    return [
        make_verified_user(
            f"pool_user_{index}",
            ip_hash=ip_hash,
            registration_date=quota.today_str(),
        )
        for index in range(3)
    ]


def test_同一IP免费提问合计15次被拦(make_verified_user):
    ip_hash = request_ip.hash_ip("198.51.100.88")
    users = _three_free_users_same_ip(make_verified_user, ip_hash)
    for user in users:
        for _ in range(5):
            quota.record_question(user["id"])

    fourth = make_verified_user(
        "pool_fourth", ip_hash=ip_hash, registration_date=quota.today_str()
    )
    snap = quota.snapshot(fourth["id"], "free", ip_hash=ip_hash)
    assert snap["remaining_questions"] == 5
    assert snap["ip"]["remaining_questions"] == 0
    ok, msg = quota.check_question(snap)
    assert ok is False
    assert "该网络" in msg and "15 次" in msg


def test_同一IP免费摘要合计15次被拦(make_verified_user):
    ip_hash = request_ip.hash_ip("198.51.100.89")
    users = _three_free_users_same_ip(make_verified_user, ip_hash)
    for user in users:
        for _ in range(5):
            quota.record_summary(user["id"])

    fourth = make_verified_user(
        "pool_summary_fourth",
        ip_hash=ip_hash,
        registration_date=quota.today_str(),
    )
    ok, msg = quota.check_summary(
        quota.snapshot(fourth["id"], "free", ip_hash=ip_hash)
    )
    assert ok is False
    assert "该网络" in msg and "15 次" in msg


def test_同一IP免费新传3次与9个文档双限(make_verified_user):
    ip_hash = request_ip.hash_ip("198.51.100.90")
    users = _three_free_users_same_ip(make_verified_user, ip_hash)
    for user in users:
        quota.record_upload(user["id"])

    fourth = make_verified_user(
        "pool_upload_fourth",
        ip_hash=ip_hash,
        registration_date=quota.today_str(),
    )
    ok, msg = quota.check_upload(
        quota.snapshot(fourth["id"], "free", ip_hash=ip_hash)
    )
    assert ok is False
    assert "新传文档额度" in msg and "3 个" in msg

    for user in users:
        for index in range(3):
            db.create_document(user["id"], f"{user['username']}_{index}.pdf")
    ok, msg = quota.check_upload(
        quota.snapshot(fourth["id"], "free", ip_hash=ip_hash)
    )
    assert ok is False
    assert "文档存储已满" in msg and "9 个" in msg


def test_付费账号不受免费IP池15次限制(make_verified_user):
    ip_hash = request_ip.hash_ip("198.51.100.91")
    users = _three_free_users_same_ip(make_verified_user, ip_hash)
    for user in users:
        for _ in range(5):
            quota.record_summary(user["id"])

    paid = make_verified_user(
        "paid_on_same_ip",
        tier="paid",
        ip_hash=ip_hash,
        registration_date=quota.today_str(),
    )
    snap = quota.snapshot(paid["id"], "paid", ip_hash=ip_hash)
    assert snap["ip"] is None
    assert snap["remaining_summaries"] == 20

    for _ in range(20):
        quota.record_summary(paid["id"])
    ok, msg = quota.check_summary(
        quota.snapshot(paid["id"], "paid", ip_hash=ip_hash)
    )
    assert ok is False
    assert "付费版每天最多 20 次" in msg
    assert "15 次" not in msg
