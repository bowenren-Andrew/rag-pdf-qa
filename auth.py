# -*- coding: utf-8 -*-
"""
auth.py（v3）
职责：账号体系与密钥安全（单一职责：不含界面、不含向量与问答逻辑）。
- 密码：hashlib.pbkdf2_hmac("sha256", 随机 16 字节盐, 480000 次迭代)，盐与哈希一起存库；
        校验用 hmac.compare_digest 做定长时间比较，避免时序侧信道。
- 用户的 DeepSeek Key：用 cryptography 的 Fernet 对称加密后落库，密钥来自环境变量 APP_SECRET_KEY。
- 所有面向用户的失败原因都抛出 AuthFriendlyError（中文提示），界面层直接展示。
"""
import hashlib
import hmac
import os
import re
import secrets

from cryptography.fernet import Fernet, InvalidToken

import db

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# PBKDF2 迭代次数（OWASP 建议 2023 年起 sha256 不少于 600000，此处取 480000 兼顾云端算力）
PBKDF2_ITERATIONS = 480_000
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


class AuthFriendlyError(Exception):
    """面向用户的中文友好错误，界面直接 str(e) 展示即可。"""


def _get_secret(name: str) -> str:
    """
    读取敏感配置，优先级：系统环境变量（云端 Secrets 会同步进来）> 项目根目录 .env。
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        from dotenv import dotenv_values

        return str(
            dotenv_values(os.path.join(PROJECT_ROOT, ".env")).get(name) or ""
        ).strip()
    except Exception:
        return ""


# ---------------- 密码哈希 ----------------
def hash_password(plain: str) -> str:
    """
    生成密码哈希。格式：pbkdf2_sha256$迭代次数$盐(hex)$哈希(hex)
    盐每次随机生成，因此同样的密码两次哈希结果不同（防止彩虹表批量比对）。
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", plain.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    """校验密码是否正确；格式异常一律判定为失败，不让程序崩溃。"""
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            plain.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        # compare_digest：定长时间比较，比 == 更安全
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


# ---------------- 输入校验 ----------------
def validate_username(username: str) -> tuple[bool, str]:
    """用户名规则：3–20 位，只允许字母、数字、下划线。"""
    username = (username or "").strip()
    if not username:
        return False, "用户名不能为空。"
    if len(username) < 3 or len(username) > 20:
        return False, "用户名长度需为 3–20 个字符。"
    if not _USERNAME_RE.match(username):
        return False, "用户名只能包含字母、数字和下划线。"
    return True, ""


def validate_password(password: str) -> tuple[bool, str]:
    """密码规则：至少 8 位。"""
    if not password or len(password) < 8:
        return False, "密码至少需要 8 位。"
    return True, ""


# ---------------- 注册与登录 ----------------
def register(username: str, password: str) -> dict:
    """
    注册新用户。
    :return: {"ok": bool, "msg": str, "user": dict|None}
    """
    username = (username or "").strip()
    ok, msg = validate_username(username)
    if not ok:
        return {"ok": False, "msg": msg, "user": None}
    ok, msg = validate_password(password)
    if not ok:
        return {"ok": False, "msg": msg, "user": None}

    if db.get_user_by_username(username):
        return {"ok": False, "msg": "该用户名已被注册，请换一个。", "user": None}

    try:
        user = db.create_user(username, hash_password(password))
    except Exception as e:
        return {"ok": False, "msg": f"注册失败，请稍后重试。（{e}）", "user": None}
    return {"ok": True, "msg": "注册成功，请登录。", "user": user}


def login(username: str, password: str) -> dict | None:
    """
    登录校验。失败统一返回 None —— 不区分"用户名不存在"与"密码错误"，
    避免攻击者据此枚举用户名。
    """
    username = (username or "").strip()
    user = db.get_user_by_username(username)
    if user is None:
        # 用户不存在时也跑一次哈希，让响应时间与密码错误时接近（防时序探测）
        hash_password(password or "")
        return None
    if not verify_password(password or "", user["password_hash"]):
        return None
    return user


def change_password(user_id: int, old_password: str, new_password: str) -> tuple[bool, str]:
    """修改密码：需验证旧密码，新密码至少 8 位。"""
    user = db.get_user_by_id(user_id)
    if user is None:
        return False, "用户不存在。"
    if not verify_password(old_password or "", user["password_hash"]):
        return False, "旧密码不正确。"
    ok, msg = validate_password(new_password)
    if not ok:
        return False, msg
    db.update_password_hash(user_id, hash_password(new_password))
    return True, "密码修改成功，请重新登录。"


# ---------------- API Key 加解密 ----------------
def _fernet() -> Fernet:
    """构造 Fernet 实例；未配置 APP_SECRET_KEY 时给出可操作的中文提示。"""
    key = _get_secret("APP_SECRET_KEY")
    if not key:
        raise AuthFriendlyError(
            "服务端未配置 APP_SECRET_KEY，无法加密保存你的 Key。"
            "本地请在 .env 中加一行（生成命令）：\n"
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode("utf-8"))
    except Exception:
        raise AuthFriendlyError(
            "APP_SECRET_KEY 不是合法的 Fernet 密钥，请重新生成：\n"
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )


def encrypt_key(plain: str) -> str:
    """加密明文 Key，返回密文字符串（bytes → str）。"""
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_key(cipher: str) -> str:
    """解密得到明文 Key；密文损坏时给出中文提示。"""
    try:
        return _fernet().decrypt(cipher.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise AuthFriendlyError(
            "你的 API Key 解密失败（可能是服务端密钥已更换），请在设置页重新填写。"
        )


def mask_key(plain: str) -> str:
    """
    把 Key 显示成掩码形式：保留前 4 位和后 4 位，中间打码。
    未设置时返回「未设置」，短 Key 只保留首位。
    """
    key = (plain or "").strip()
    if not key:
        return "未设置"
    if len(key) <= 8:
        return key[0] + "*" * (len(key) - 1)
    return f"{key[:4]}{'*' * 8}{key[-4:]}"


# ---------------- 用户 Key 的读写 ----------------
def get_user_api_key(user_id: int) -> str:
    """读取并解密当前用户的 API Key；未设置或解密失败返回空字符串。"""
    user = db.get_user_by_id(user_id)
    if user is None or not user.get("api_key_enc"):
        return ""
    try:
        return decrypt_key(user["api_key_enc"])
    except AuthFriendlyError:
        return ""  # 解密失败当作未设置处理，界面会引导用户重新填写


def set_user_api_key(user_id: int, plain: str) -> str:
    """加密保存用户的 API Key，返回掩码形式用于界面回显。"""
    plain = (plain or "").strip()
    if not plain:
        raise AuthFriendlyError("请输入要保存的 API Key。")
    db.update_api_key_enc(user_id, encrypt_key(plain))
    return mask_key(plain)


def delete_user_api_key(user_id: int):
    """删除用户已保存的 Key（置空）。"""
    db.update_api_key_enc(user_id, None)
