# -*- coding: utf-8 -*-
"""
auth.py 的单元测试（密码哈希 / 平台 Key 读取 / 历史 Key 清理 / 注册校验）

测试原则：
1. 用 conftest 的 tmp_db fixture 指向临时 sqlite，不碰本地 app.db，更不连云端数据库；
2. 不依赖真实 .env 里的 DEEPSEEK_API_KEY；
3. 不发任何网络请求，因此不会调用真实的 DeepSeek API。
"""
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

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
def test_用户名登录成功(register_verified):
    """注册后可以用用户名登录。"""
    result = register_verified("alice", "AlicePass123")
    assert result["ok"] is True
    assert result["user"]["username"] == "alice"
    assert result["user"]["email_verified_at"] is not None

    user = auth.login("alice", "AlicePass123")
    assert user is not None
    assert user["id"] == result["user"]["id"]


def test_邮箱登录成功(register_verified):
    """注册后可以用完整邮箱登录；邮箱大小写不敏感。"""
    result = register_verified("bobby", "BobbyPass123")
    email = result["user"]["email"]
    assert email

    user = auth.login(email, "BobbyPass123")
    assert user is not None
    assert user["id"] == result["user"]["id"]
    assert auth.login(email.upper(), "BobbyPass123")["id"] == result["user"]["id"]


def test_邮箱不存在时登录失败(tmp_db):
    """不存在的邮箱返回 None，不抛异常。"""
    assert auth.login("nobody@example.com", "Password123") is None


def test_邮箱登录密码错误时返回None(register_verified):
    """邮箱存在但密码错误，与用户名密码错误一样返回 None。"""
    result = register_verified("wrongpass", "Wrongpass123")
    assert auth.login(result["user"]["email"], "bad-password") is None


def test_未验证邮箱账号用邮箱登录被拦回(tmp_db):
    """邮箱未验证的账号即使密码正确，也要用中文提示拦回。"""
    db.create_user(
        "unverified_email",
        auth.hash_password("Password123"),
        email="unverified@example.com",
        email_verified_at=None,
    )
    with pytest.raises(auth.AuthFriendlyError, match="邮箱验证"):
        auth.login("unverified@example.com", "Password123")


# ---------------- 邮箱登录：归一化与跨库一致性（回归） ----------------
def test_邮箱登录遇到库里未规范化的历史数据也能成功(tmp_db):
    """
    回归：线上历史行的 email 可能存成混合大小写 / 带首尾空格。
    原来用精确等号比对（Postgres 区分大小写），这类数据直接查不到；
    现在按「小写 + 去首尾空白」归一后比对，输入怎么写都能命中。
    """
    db.create_user(
        "legacy_mixed_case",
        auth.hash_password("LegacyPass123"),
        email="  BowenRen9@Gmail.com ",
        email_verified_at=datetime.now(),
    )
    for identifier in (
        "bowenren9@gmail.com",
        "BowenRen9@Gmail.com",
        "BOWENREN9@GMAIL.COM",
        "  bowenren9@gmail.com  ",
    ):
        user = auth.login(identifier, "LegacyPass123")
        assert user is not None, identifier
        assert user["username"] == "legacy_mixed_case"


def test_邮箱里的零宽字符不影响登录(tmp_db):
    """复制粘贴带进来的零宽字符（U+200B）也应被忽略，否则线上会「看起来一模一样却查不到」。"""
    db.create_user(
        "zw_user",
        auth.hash_password("ZwPass12345"),
        email="bowenren9@gmail.com\u200b",
        email_verified_at=datetime.now(),
    )
    user = auth.login("bowenren9@gmail.com", "ZwPass12345")
    assert user is not None
    assert user["username"] == "zw_user"


def test_同邮箱大小写不同时登录不抛异常(tmp_db):
    """
    唯一索引区分大小写，库里可能同时存在两条同邮箱账号。
    归一后可能匹配多行，登录必须稳定取最早一条，不能抛 MultipleResultsFound。
    """
    db.create_user(
        "dup_first",
        auth.hash_password("FirstPass123"),
        email="bowenren9@gmail.com",
        email_verified_at=datetime.now(),
    )
    db.create_user(
        "dup_second",
        auth.hash_password("SecondPass123"),
        email="BowenRen9@Gmail.com",
        email_verified_at=datetime.now(),
    )
    user = auth.login("bowenren9@gmail.com", "FirstPass123")
    assert user is not None
    assert user["username"] == "dup_first"
    assert auth.login("bowenren9@gmail.com", "SecondPass123") is None


def test_邮箱查询在sqlite与postgres下生成同一语义的sql():
    """
    回归：邮箱查询不能用 LIKE/ILIKE。
    sqlite 的 LIKE 默认大小写不敏感、Postgres 的 LIKE 大小写敏感，
    同一句代码在两边行为不同；现在两种方言编译出的 SQL 完全一致。
    """
    stmt = select(db.User.id).where(
        db._email_column_key(db.User.email) == "bowenren9@gmail.com"
    )
    compiled = {}
    for name, dialect in (
        ("postgres", postgresql.dialect()),
        ("sqlite", sqlite.dialect()),
    ):
        sql = str(stmt.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
        compiled[name] = " ".join(sql.split())
        lowered = compiled[name].lower()
        assert " like " not in lowered
        assert " ilike " not in lowered
        assert "lower(trim(" in lowered
    assert compiled["postgres"] == compiled["sqlite"]


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


