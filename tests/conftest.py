# -*- coding: utf-8 -*-
"""
pytest 共用配置
作用：
1. 把项目根目录（tests 的上一级）加入 sys.path，测试才能 import 到 loader / qa / db / auth；
2. 提供两个共用 fixture：临时 sqlite 数据库、随机 Fernet 密钥。
注意：这里不加载向量模型、不发起任何真实网络请求。
"""
import os
import sys
from datetime import datetime

import pytest
from cryptography.fernet import Fernet

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import db  # noqa: E402（必须在 sys.path 处理之后导入）
import auth  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path):
    """
    把数据库指向一个临时 sqlite 文件，测完自动清理。
    避免测试污染项目里的 app.db，也不连接任何真实 Postgres。
    """
    url = f"sqlite:///{tmp_path / 'test.db'}"
    db.set_database_url(url)
    db.init_db()
    yield url
    db.set_database_url(f"sqlite:///{os.path.join(PROJECT_ROOT, 'app.db')}")


@pytest.fixture
def fernet_key(monkeypatch):
    """注入一个随机 Fernet 密钥到环境变量，保证加密相关测试不依赖真实 .env。"""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_SECRET_KEY", key)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return key


@pytest.fixture
def register_verified(tmp_db, monkeypatch):
    """
    走完整的「发验证码 -> Turnstile 通过 -> 注册」流程，返回 auth.register 的结果。
    发送邮件函数被替换成空实现，不发起任何真实网络请求。
    """
    if not os.environ.get("APP_SECRET_KEY"):
        monkeypatch.setenv("APP_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(
        auth.turnstile, "verify_token", lambda token, remote_ip=None: (True, "")
    )
    monkeypatch.setattr(auth, "_new_verification_code", lambda: "123456")
    state = {"n": 0}

    def _register(username, password, email=None, client_ip=None):
        state["n"] += 1
        email = email or f"{username}{state['n']}@example.com"
        client_ip = client_ip or f"203.0.113.{state['n'] % 250 + 1}"
        sent = auth.request_email_code(email, sender=lambda _email, _code: None)
        assert sent["ok"], sent
        return auth.register(
            username,
            password,
            email=email,
            verification_code="123456",
            turnstile_token="test-token",
            client_ip=client_ip,
        )

    return _register


@pytest.fixture
def make_verified_user(tmp_db):
    """直接创建已验证账号（用于不关心注册流程的测试）。"""
    state = {"n": 0}

    def _make(
        username,
        password="password123",
        tier="free",
        ip_hash=None,
        registration_date=None,
        email=None,
    ):
        state["n"] += 1
        user = db.create_user(
            username,
            auth.hash_password(password),
            email=email or f"{username}{state['n']}@example.com",
            email_verified_at=datetime.now(),
            registration_ip_hash=ip_hash,
            registration_date=registration_date,
            max_registrations_per_ip=1000,
        )
        if tier != "free":
            db.set_user_tier(user["id"], tier)
        return db.get_user_by_id(user["id"])

    return _make
