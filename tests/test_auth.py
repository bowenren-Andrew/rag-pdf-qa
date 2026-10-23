# -*- coding: utf-8 -*-
"""
auth.py 的单元测试（密码哈希 / Key 加解密 / 掩码 / 注册校验）

测试原则：
1. 用 conftest 的 tmp_db fixture 指向临时 sqlite，不碰本地 app.db，更不连云端数据库；
2. 用随机生成的 Fernet 密钥做加解密测试，不使用真实 APP_SECRET_KEY；
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


# ---------------- API Key 加解密与掩码 ----------------
def test_Key加密后可以解密还原(fernet_key):
    """Fernet 加密后再解密，必须得到原文。"""
    plain = "sk-unit-test-key-abcdefg"
    cipher = auth.encrypt_key(plain)
    assert cipher != plain  # 密文当然不能等于明文
    assert auth.decrypt_key(cipher) == plain


def test_掩码只保留前四位和后四位(fernet_key):
    """设置页展示的 Key 必须打码：前 4 位 + 星号 + 后 4 位。"""
    masked = auth.mask_key("sk-ab60751e3d6b46628308b1ef631377f2")
    assert masked.startswith("sk-a")
    assert masked.endswith("77f2")
    assert "60751e3d" not in masked  # 中间敏感部分不能出现
    assert "*" in masked


def test_未设置Key时掩码显示为未设置(fernet_key):
    """没填 Key 时展示「未设置」，而不是空字符串。"""
    assert auth.mask_key("") == "未设置"
    assert auth.mask_key("   ") == "未设置"


def test_未配置服务密钥时给出中文提示(monkeypatch):
    """APP_SECRET_KEY 缺失时，应抛出带操作指引的中文错误。"""
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    # 连 .env 兜底一起屏蔽，模拟“服务端完全没配密钥”的场景
    monkeypatch.setattr(auth, "_get_secret", lambda name: "")
    with pytest.raises(auth.AuthFriendlyError, match="APP_SECRET_KEY"):
        auth.encrypt_key("sk-some-key")


# ---------------- 注册与登录（走数据库） ----------------
def test_注册成功后可以登录(tmp_db):
    """正常注册后应能用同一组账号密码登录成功。"""
    result = auth.register("alice", "AlicePass123")
    assert result["ok"] is True
    assert result["user"]["username"] == "alice"

    user = auth.login("alice", "AlicePass123")
    assert user is not None
    assert user["id"] == result["user"]["id"]


def test_用户名重复时给出中文提示(tmp_db):
    """重名不能静默覆盖，要返回可展示的中文原因。"""
    auth.register("alice", "AlicePass123")
    duplicated = auth.register("alice", "AnotherPass123")
    assert duplicated["ok"] is False
    assert "已被注册" in duplicated["msg"]


def test_登录失败不区分用户名不存在和密码错误(tmp_db):
    """两种失败都返回 None，界面统一提示「用户名或密码不正确」。"""
    auth.register("bob", "BobPass123456")
    assert auth.login("bob", "wrong-password") is None        # 密码错
    assert auth.login("not_exist_user", "BobPass123456") is None  # 用户不存在


def test_修改密码需要正确的旧密码(tmp_db):
    """改密码必须验证旧密码，新密码同样要满足 8 位规则。"""
    user = auth.register("carol", "CarolPass123")["user"]
    ok, msg = auth.change_password(user["id"], "wrong-old", "NewPass123456")
    assert ok is False and "旧密码不正确" in msg

    ok, msg = auth.change_password(user["id"], "CarolPass123", "short")
    assert ok is False and "8" in msg

    ok, msg = auth.change_password(user["id"], "CarolPass123", "NewPass123456")
    assert ok is True
    assert auth.login("carol", "NewPass123456") is not None


def test_用户Key存取与删除(tmp_db, fernet_key):
    """设置页保存的 Key 应能解密读回，删除后读为空字符串。"""
    user = auth.register("dave", "DavePass123")["user"]
    auth.set_user_api_key(user["id"], "sk-dave-secret-key")
    assert auth.get_user_api_key(user["id"]) == "sk-dave-secret-key"

    auth.delete_user_api_key(user["id"])
    assert auth.get_user_api_key(user["id"]) == ""


def test_密文确实写进了数据库(tmp_db, fernet_key):
    """落库的必须是密文，不能直接是明文 Key。"""
    user = auth.register("erin", "ErinPass123")["user"]
    auth.set_user_api_key(user["id"], "sk-erin-plain-key")
    stored = db.get_user_by_id(user["id"])
    assert stored["api_key_enc"] != "sk-erin-plain-key"
    assert "sk-erin-plain-key" not in stored["api_key_enc"]
