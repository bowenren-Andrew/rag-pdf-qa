# -*- coding: utf-8 -*-
"""
auth.py 的单元测试（密码哈希 / 平台 Key 读取 / 历史 Key 清理 / 注册校验）

测试原则：
1. 用 conftest 的 tmp_db fixture 指向临时 sqlite，不碰本地 app.db，更不连云端数据库；
2. 不依赖真实 .env 里的 DEEPSEEK_API_KEY；
3. 不发任何网络请求，因此不会调用真实的 DeepSeek API。
"""
import pytest

import auth
import db


# ---------------- 密码哈希 ----------------
def test_密码哈希后不等于明文():
    """存库的一定是哈希值，绝不能出现明文密码。"""
    hashed = auth.hash_password("MyPass123456")
    assert hashed != "MyPass123456"
    assert "MyPass123456" not in hashed
    assert hashed.startswith("pbkdf2_sha256$")


def test_正确密码可以通过校验():
    """正常登录路径：同一明文应能通过哈希校验。"""
    hashed = auth.hash_password("MyPass123456")
    assert auth.verify_password("MyPass123456", hashed) is True


def test_错误密码不能通过校验():
    """密码写错必须校验失败，返回 False 而不是抛异常。"""
    hashed = auth.hash_password("MyPass123456")
    assert auth.verify_password("WrongPass123456", hashed) is False
    assert auth.verify_password("", hashed) is False


def test_相同密码两次哈希结果不同因为盐不同():
    """每次哈希用随机盐，防止用彩虹表批量比对相同密码的用户。"""
    assert auth.hash_password("MyPass123456") != auth.hash_password("MyPass123456")


def test_哈希格式损坏时不崩溃():
    """数据库里万一存了脏数据，校验应判 False 而不是让程序崩。"""
    assert auth.verify_password("MyPass123456", "一段损坏的数据") is False


# ---------------- 用户名与密码规则 ----------------
@pytest.mark.parametrize(
    "username, ok",
    [("ab", False), ("abc", True), ("Zhang_01", True), ("中文名", False), ("a-b", False), ("a" * 21, False)],
)
def test_用户名规则校验(username, ok):
    """用户名必须是 3–20 位字母、数字或下划线。"""
    assert auth.validate_username(username)[0] is ok


def test_密码少于8位被拒绝():
    """密码强度下限：至少 8 位。"""
    assert auth.validate_password("1234567")[0] is False
    assert auth.validate_password("12345678")[0] is True


# ---------------- 平台 Key 读取 ----------------
def test_平台Key从环境变量读取(monkeypatch):
    """统一 Key 只从平台配置读取。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-platform-test-123456")
    assert auth.get_platform_api_key() == "sk-platform-test-123456"


def test_平台Key未配置时返回空字符串(monkeypatch):
    """服务端没配平台 Key 时返回空字符串，由界面给出中文提示。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(auth, "_get_secret", lambda name: "")
    assert auth.get_platform_api_key() == ""


def test_启动迁移清空历史用户Key(tmp_db):
    """老库的 users.api_key_enc 必须在启动迁移时置空，不能继续留在库里。"""
    with db.get_engine().begin() as conn:
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN api_key_enc TEXT")
        conn.exec_driver_sql(
            "INSERT INTO users (username, password_hash, api_key_enc, tier, created_at) "
            "VALUES ('legacy_key_user', 'x', 'legacy-cipher', 'free', CURRENT_TIMESTAMP)"
        )

    db.init_db()

    with db.get_engine().connect() as conn:
        value = conn.exec_driver_sql(
            "SELECT api_key_enc FROM users WHERE username = 'legacy_key_user'"
        ).scalar()
    assert value is None


# ---------------- 注册与登录（走数据库） ----------------
def test_注册成功后可以登录(register_verified):
    """正常注册后应能用同一组账号密码登录成功。"""
    result = register_verified("alice", "AlicePass123")
    assert result["ok"] is True
    assert result["user"]["username"] == "alice"
    assert result["user"]["email_verified_at"] is not None

    user = auth.login("alice", "AlicePass123")
    assert user is not None
    assert user["id"] == result["user"]["id"]


def test_用户名重复时给出中文提示(register_verified):
    """重名不能静默覆盖，要返回可展示的中文原因。"""
    register_verified("alice", "AlicePass123")
    duplicated = register_verified("alice", "AnotherPass123")
    assert duplicated["ok"] is False
    assert "已被注册" in duplicated["msg"]


def test_登录失败不区分用户名不存在和密码错误(register_verified):
    """两种失败都返回 None，界面统一提示「用户名或密码不正确」。"""
    register_verified("bob", "BobPass123456")
    assert auth.login("bob", "wrong-password") is None        # 密码错
    assert auth.login("not_exist_user", "BobPass123456") is None  # 用户不存在


def test_修改密码需要正确的旧密码(register_verified):
    """改密码必须验证旧密码，新密码同样要满足 8 位规则。"""
    user = register_verified("carol", "CarolPass123")["user"]
    ok, msg = auth.change_password(user["id"], "wrong-old", "NewPass123456")
    assert ok is False and "旧密码不正确" in msg

    ok, msg = auth.change_password(user["id"], "CarolPass123", "short")
    assert ok is False and "8" in msg

    ok, msg = auth.change_password(user["id"], "CarolPass123", "NewPass123456")
    assert ok is True
    assert auth.login("carol", "NewPass123456") is not None


