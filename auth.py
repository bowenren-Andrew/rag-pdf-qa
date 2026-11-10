# -*- coding: utf-8 -*-
"""
auth.py（v3.10）
职责：账号体系与平台密钥读取（单一职责：不含界面、不含向量与问答逻辑）。
- 密码：hashlib.pbkdf2_hmac("sha256", 随机 16 字节盐, 480000 次迭代)，盐与哈希一起存库；
        校验用 hmac.compare_digest 做定长时间比较，避免时序侧信道。
- DeepSeek Key：只读取平台统一 Key（Streamlit Secrets / 环境变量 / 本地 .env），
        不再接收、加密或保存任何用户自填 Key。
- 所有面向用户的失败原因都抛出 AuthFriendlyError（中文提示），界面层直接展示。
- 登录限流：连续失败 5 次按账号锁定 15 分钟（进程内计数，重启后清零）。
"""
import hashlib
import hmac
import math
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import db
import mailer
import request_ip
import turnstile

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# PBKDF2 迭代次数（OWASP 建议 2023 年起 sha256 不少于 600000，此处取 480000 兼顾云端算力）
PBKDF2_ITERATIONS = 480_000
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")

# 邮箱校验：常规 RFC 5322 的简化版，够拦住明显错误，又不至于误伤正常地址。
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)

# 常见一次性/临时邮箱域名（含子域名），注册时拒绝。
DISPOSABLE_EMAIL_DOMAINS = {
    "10minutemail.com",
    "20minutemail.com",
    "33mail.com",
    "dispostable.com",
    "emailondeck.com",
    "fakeinbox.com",
    "getnada.com",
    "guerrillamail.com",
    "guerrillamail.info",
    "guerrillamail.net",
    "guerrillamail.org",
    "maildrop.cc",
    "mailinator.com",
    "mailnesia.com",
    "mintemail.com",
    "mytemp.email",
    "sharklasers.com",
    "spam4.me",
    "temp-mail.org",
    "tempail.com",
    "tempmail.com",
    "tempmail.net",
    "throwawaymail.com",
    "trashmail.com",
    "yopmail.com",
}

EMAIL_CODE_LENGTH = 6
EMAIL_CODE_TTL_MINUTES = 10
EMAIL_CODE_RESEND_SECONDS = 60
EMAIL_CODE_DAILY_LIMIT = 5
EMAIL_CODE_MAX_ATTEMPTS = 5
MAX_REGISTRATIONS_PER_IP_PER_DAY = 3

BEIJING_TZ = timezone(timedelta(hours=8))

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


def normalize_email(email: str) -> str:
    """邮箱统一小写、去首尾空白与零宽字符；入库、查重、验证码、登录都用同一形式。"""
    return db.canonical_email(email)


def validate_email(email: str) -> tuple[bool, str]:
    """校验邮箱格式，并拒绝常见一次性/临时邮箱域名。"""
    email = normalize_email(email)
    if not email:
        return False, "请输入邮箱。"
    if len(email) > 254:
        return False, "邮箱地址过长，请检查后重新填写。"
    local, _, domain = email.rpartition("@")
    if not local or len(local) > 64 or not _EMAIL_RE.match(email):
        return False, "邮箱格式不正确，请检查后重新填写。"
    if any(
        domain == blocked or domain.endswith("." + blocked)
        for blocked in DISPOSABLE_EMAIL_DOMAINS
    ):
        return False, "不支持一次性/临时邮箱，请使用常用邮箱注册。"
    return True, ""


def _utcnow() -> datetime:
    """数据库统一存不带时区的 UTC 时间，便于不同部署环境比较。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _beijing_today() -> str:
    """北京时间的日期字符串，用于每日发送/注册次数统计。"""
    return datetime.now(BEIJING_TZ).date().isoformat()


def _verification_code_hash(email: str, code: str) -> str:
    """验证码只存 HMAC-SHA256；数据库泄露也无法直接拿到 6 位明文码。"""
    secret = _get_secret("APP_SECRET_KEY")
    if not secret:
        raise AuthFriendlyError(
            "服务端未配置 APP_SECRET_KEY，暂时无法发送验证码，请联系管理员。"
        )
    return hmac.new(
        secret.encode("utf-8"),
        f"{normalize_email(email)}:{code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _new_verification_code() -> str:
    """生成 6 位数字验证码（保留前导 0）。"""
    return f"{secrets.randbelow(10 ** EMAIL_CODE_LENGTH):0{EMAIL_CODE_LENGTH}d}"


def request_email_code(email: str, sender=None) -> dict:
    """
    发送邮箱验证码。
    :param sender: 可注入的发送函数（测试用），默认 mailer.send_verification_code。
    :return: {"ok": bool, "msg": str}
    """
    email = normalize_email(email)
    ok, msg = validate_email(email)
    if not ok:
        return {"ok": False, "msg": msg}

    now = _utcnow()
    if db.count_recent_email_codes(
        email, now - timedelta(seconds=EMAIL_CODE_RESEND_SECONDS)
    ):
        return {"ok": False, "msg": "验证码发送太频繁，请 1 分钟后再试。"}
    if db.count_email_codes_on_date(email, _beijing_today()) >= EMAIL_CODE_DAILY_LIMIT:
        return {
            "ok": False,
            "msg": f"今天该邮箱发送验证码次数已达上限（{EMAIL_CODE_DAILY_LIMIT} 次），请明天再试。",
        }

    code_id = None
    try:
        code = _new_verification_code()
        code_hash = _verification_code_hash(email, code)
        code_id = db.create_email_verification_code(
            email=email,
            code_hash=code_hash,
            request_date=_beijing_today(),
            created_at=now,
            expires_at=now + timedelta(minutes=EMAIL_CODE_TTL_MINUTES),
        )
        (sender or mailer.send_verification_code)(email, code)
    except AuthFriendlyError as e:
        return {"ok": False, "msg": str(e)}
    except mailer.MailerError as e:
        if code_id is not None:
            db.delete_email_verification_code(code_id)
        return {"ok": False, "msg": str(e)}
    except Exception:
        if code_id is not None:
            db.delete_email_verification_code(code_id)
        return {"ok": False, "msg": "验证码发送失败，请稍后重试。"}
    return {
        "ok": True,
        "msg": "验证码已发送，请查收邮箱（10 分钟内有效）。",
        "email": email,
    }


def is_email_verified(user: dict | None) -> bool:
    """账号是否完成邮箱验证；历史账号在迁移时已补上验证时间。"""
    return bool(user and user.get("email_verified_at") is not None)


# ---------------- 注册与登录 ----------------
def register(
    username: str,
    password: str,
    email: str = "",
    verification_code: str = "",
    turnstile_token: str = "",
    client_ip: str | None = None,
) -> dict:
    """
    注册新用户（v3.9：必须完成 Turnstile + 邮箱验证码校验）。
    :return: {"ok": bool, "msg": str, "user": dict|None}
    """
    username = (username or "").strip()
    ok, msg = validate_username(username)
    if not ok:
        return {"ok": False, "msg": msg, "user": None}
    ok, msg = validate_password(password)
    if not ok:
        return {"ok": False, "msg": msg, "user": None}

    email = normalize_email(email)
    ok, msg = validate_email(email)
    if not ok:
        return {"ok": False, "msg": msg, "user": None}

    # 先做人机验证：token 缺失/错误都只给中文提示。
    verified, verify_msg = turnstile.verify_token(turnstile_token, client_ip)
    if not verified:
        return {"ok": False, "msg": verify_msg, "user": None}

    ip_hash = request_ip.hash_ip(client_ip)
    registration_date = _beijing_today()
    if (
        ip_hash
        and db.count_users_registered_from_ip(ip_hash, registration_date)
        >= MAX_REGISTRATIONS_PER_IP_PER_DAY
    ):
        return {
            "ok": False,
            "msg": f"今天该网络已注册满 {MAX_REGISTRATIONS_PER_IP_PER_DAY} 个，明天再试",
            "user": None,
        }

    if db.get_user_by_username(username):
        return {"ok": False, "msg": "该用户名已被注册，请换一个。", "user": None}
    if db.get_user_by_email(email):
        return {"ok": False, "msg": "该邮箱已注册，请直接登录或换一个邮箱。", "user": None}

    code = (verification_code or "").strip()
    if not re.fullmatch(rf"\d{{{EMAIL_CODE_LENGTH}}}", code):
        return {"ok": False, "msg": "请输入 6 位数字邮箱验证码。", "user": None}
    try:
        code_hash = _verification_code_hash(email, code)
    except AuthFriendlyError as e:
        return {"ok": False, "msg": str(e), "user": None}

    now = _utcnow()
    code_result = db.consume_email_verification_code(
        email, code_hash, now, max_attempts=EMAIL_CODE_MAX_ATTEMPTS
    )
    if not code_result["ok"]:
        reason = code_result["reason"]
        if reason == "none":
            message = "请先获取邮箱验证码。"
        elif reason == "expired":
            message = "验证码已过期，请重新获取。"
        elif reason == "attempts":
            message = "验证码错误次数过多，请重新获取。"
        else:
            remaining = max(EMAIL_CODE_MAX_ATTEMPTS - int(code_result["attempts"]), 0)
            message = f"验证码不正确，还可再试 {remaining} 次。"
        return {"ok": False, "msg": message, "user": None}

    try:
        user = db.create_user(
            username,
            hash_password(password),
            email=email,
            email_verified_at=now,
            registration_ip_hash=ip_hash or None,
            registration_date=registration_date if ip_hash else None,
            max_registrations_per_ip=MAX_REGISTRATIONS_PER_IP_PER_DAY,
        )
    except db.IpRegistrationLimitError:
        return {
            "ok": False,
            "msg": f"今天该网络已注册满 {MAX_REGISTRATIONS_PER_IP_PER_DAY} 个，明天再试",
            "user": None,
        }
    except Exception:
        # 唯一索引兜底：并发下用户名/邮箱可能刚好被别人抢先注册。
        return {
            "ok": False,
            "msg": "用户名或邮箱已被注册，请换一个再试。",
            "user": None,
        }
    return {"ok": True, "msg": "注册成功，请登录。", "user": user}


def login(username: str, password: str) -> dict | None:
    """
    登录校验，支持用户名或完整邮箱地址。
    先按用户名查，查不到再按邮箱查（邮箱大小写、首尾空白都不敏感，
    由 db.get_user_by_email 统一归一后比对）。
    失败统一返回 None —— 不区分"账号不存在"与"密码错误"，
    避免攻击者据此枚举账号。
    """
    identifier = (username or "").strip()
    user = db.get_user_by_username(identifier)
    if user is None and "@" in identifier:
        user = db.get_user_by_email(normalize_email(identifier))
    if user is None:
        # 用户不存在时也跑一次哈希，让响应时间与密码错误时接近（防时序探测）
        hash_password(password or "")
        return None
    if not verify_password(password or "", user["password_hash"]):
        return None
    if not is_email_verified(user):
        raise AuthFriendlyError("该账号尚未完成邮箱验证，请重新注册并验证邮箱。")
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


# ---------------- 登录限流（L-05） ----------------
# 连续失败达到阈值后按账号锁定一段时间。计数放在进程内：
# 不落库、不改表结构；应用重启或重新部署后自动清零。
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 15 * 60

_login_failures: dict[str, dict] = {}
_login_failures_lock = threading.Lock()
_LOGIN_FAILURE_ENTRY_LIMIT = 1000


def _normalize_login_name(username: str) -> str:
    """锁定按账号维度统计，用户名大小写与首尾空格不区分。"""
    return (username or "").strip().lower()


def get_login_lock_remaining(username: str) -> int:
    """返回该账号剩余锁定秒数；未锁定或已过期返回 0。"""
    key = _normalize_login_name(username)
    if not key:
        return 0
    with _login_failures_lock:
        state = _login_failures.get(key)
        if not state or state["locked_until"] <= 0:
            # 尚未触发锁定：保留失败计数，不清空
            return 0
        remaining = int(math.ceil(state["locked_until"] - time.monotonic()))
        if remaining > 0:
            return remaining
        # 锁定已过期：清掉旧计数，重新开始
        _login_failures.pop(key, None)
        return 0


def record_login_failure(username: str) -> int:
    """
    记录一次登录失败。
    返回本次触发的剩余锁定秒数；未达到阈值返回 0。
    """
    key = _normalize_login_name(username)
    if not key:
        return 0
    now = time.monotonic()
    with _login_failures_lock:
        state = _login_failures.setdefault(
            key, {"count": 0, "locked_until": 0.0, "last_failure": now}
        )
        state["count"] += 1
        state["last_failure"] = now
        if state["count"] < LOGIN_MAX_FAILURES:
            return 0
        state["count"] = 0
        state["locked_until"] = now + LOGIN_LOCK_SECONDS
        _prune_login_failures(now)
        return LOGIN_LOCK_SECONDS


def clear_login_failures(username: str) -> None:
    """登录成功后清零该账号的失败计数与锁定状态。"""
    key = _normalize_login_name(username)
    if not key:
        return
    with _login_failures_lock:
        _login_failures.pop(key, None)


def _prune_login_failures(now: float) -> None:
    """条目过多时清理早已过期且不再锁定的账号，避免字典无限增长。"""
    if len(_login_failures) <= _LOGIN_FAILURE_ENTRY_LIMIT:
        return
    stale = [
        name
        for name, state in _login_failures.items()
        if state["locked_until"] <= now
        and now - state["last_failure"] > LOGIN_LOCK_SECONDS
    ]
    for name in stale:
        _login_failures.pop(name, None)


def format_lock_remaining(seconds: int) -> str:
    """把剩余秒数格式化成「X 分 Y 秒」，用于锁定提示。"""
    seconds = max(0, int(seconds))
    minutes, rest = divmod(seconds, 60)
    if minutes:
        return f"{minutes} 分 {rest} 秒"
    return f"{rest} 秒"


def get_platform_api_key() -> str:
    """
    读取平台方统一的 DeepSeek Key。
    只从环境变量 / Streamlit Secrets（经 app 同步进环境变量）/ 本地 .env 读取；
    不写进代码、不写日志、不在页面展示。
    """
    return _get_secret("DEEPSEEK_API_KEY")
