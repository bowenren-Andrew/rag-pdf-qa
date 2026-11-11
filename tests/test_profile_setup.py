# -*- coding: utf-8 -*-
"""自动建号后的用户名/密码引导，以及设置页背后的账号更新逻辑。"""
from datetime import datetime
import sqlite3

import pytest

import auth
import db


@pytest.fixture
def auto_user(tmp_db):
    return db.create_user(
        "user_abcd1234",
        None,
        email="auto_profile@example.com",
        email_verified_at=datetime.now(),
        is_auto_created=True,
    )


def test_临时用户名生成会避让已有账号(auto_user, monkeypatch):
    values = iter(["abcd1234", "deadbeef"])
    monkeypatch.setattr(auth.secrets, "token_hex", lambda _n=4: next(values))

    assert auth._new_temp_username() == "user_deadbeef"


def test_跳过首次引导保留临时用户名(auto_user):
    result = auth.finish_onboarding(auto_user["id"])

    assert result["ok"] is True
    user = db.get_user_by_id(auto_user["id"])
    assert user["username"] == "user_abcd1234"
    assert user["onboarding_completed_at"] is not None
    assert user["has_password"] is False


def test_引导里可改用户名并设置密码(auto_user):
    result = auth.complete_onboarding(
        auto_user["id"],
        "custom_profile",
        password="ProfilePass123",
        confirm_password="ProfilePass123",
    )

    assert result["ok"] is True
    user = db.get_user_by_id(auto_user["id"])
    assert user["username"] == "custom_profile"
    assert user["has_password"] is True
    assert user["onboarding_completed_at"] is not None
    assert auth.login("custom_profile", "ProfilePass123") is not None


def test_引导用户名重名被明确拒绝(auto_user):
    db.create_user("taken_name", auth.hash_password("Password123"))

    result = auth.complete_onboarding(
        auto_user["id"],
        "taken_name",
        password="",
    )

    assert result["ok"] is False
    assert "已被注册" in result["msg"]
    user = db.get_user_by_id(auto_user["id"])
    assert user["onboarding_completed_at"] is None


def test_未设置密码时用户名登录被明确拦回(auto_user):
    with pytest.raises(auth.AuthFriendlyError, match="该账号未设置密码"):
        auth.login(auto_user["username"], "anything")
    with pytest.raises(auth.AuthFriendlyError, match="该账号未设置密码"):
        auth.login("auto_profile@example.com", "anything")


def test_设置密码后用户名和邮箱密码都能登录(auto_user):
    ok, msg = auth.set_password(auto_user["id"], "NewProfilePass123")
    assert ok is True
    assert "密码设置成功" in msg

    assert auth.login(auto_user["username"], "NewProfilePass123") is not None
    assert auth.login("auto_profile@example.com", "NewProfilePass123") is not None


def test_设置页改用户名有唯一性校验(auto_user):
    db.create_user("occupied_name", auth.hash_password("Password123"))

    ok, msg = auth.change_username(auto_user["id"], "occupied_name")
    assert ok is False
    assert "已被注册" in msg

    ok, msg = auth.change_username(auto_user["id"], "fresh_name")
    assert ok is True
    assert db.get_user_by_id(auto_user["id"])["username"] == "fresh_name"


def test_老账号用户名和邮箱密码登录行为不变(make_verified_user):
    old = make_verified_user("legacy_login", password="LegacyPass123")

    assert auth.login("legacy_login", "LegacyPass123") is not None
    assert auth.login(old["email"], "LegacyPass123") is not None
    assert auth.login("legacy_login", "wrong-password") is None


def test_旧sqlite密码列迁移后仍保留账号且可创建无密码账号(tmp_db, tmp_path):
    """旧库的 password_hash NOT NULL 必须在不丢历史数据的前提下迁移掉。"""
    path = tmp_path / "legacy_users.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE users (
            id INTEGER NOT NULL,
            username VARCHAR(32) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            api_key_enc TEXT,
            created_at DATETIME NOT NULL,
            tier VARCHAR(16) NOT NULL DEFAULT 'free',
            email VARCHAR(254),
            email_verified_at TIMESTAMP,
            registration_ip_hash VARCHAR(64),
            registration_date VARCHAR(10),
            PRIMARY KEY (id)
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX ix_users_username ON users (username)"
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, created_at) "
        "VALUES ('legacy_keep', 'old-hash', '2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    db.set_database_url(f"sqlite:///{path.as_posix()}")
    db.init_db()

    assert db.get_user_by_username("legacy_keep") is not None
    created = db.create_user(
        "new_without_password",
        None,
        email="new_without_password@example.com",
        is_auto_created=True,
    )
    assert created["has_password"] is False

    check = sqlite3.connect(path)
    password_info = next(
        row for row in check.execute("PRAGMA table_info(users)")
        if row[1] == "password_hash"
    )
    check.close()
    assert password_info[3] == 0
