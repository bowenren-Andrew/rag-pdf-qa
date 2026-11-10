# -*- coding: utf-8 -*-
"""
mailer.py（v3.9 防刷注册）
职责：只负责通过 SMTP 发送邮箱验证码。不生成验证码、不读写数据库、不打日志。

配置全部来自环境变量 / Streamlit Secrets（由 app._sync_secrets_to_env 同步）：
- SMTP_HOST        SMTP 服务器地址
- SMTP_PORT        端口，默认 587
- SMTP_USER        登录账号（可选）
- SMTP_PASSWORD    登录密码 / 授权码（绝不打印）
- SMTP_FROM        发件人，默认等于 SMTP_USER
- SMTP_SECURITY    ssl / starttls / none，默认 465 用 ssl、其余用 starttls
- SMTP_TIMEOUT     超时秒数，默认 10
"""
import os
import smtplib
import ssl
from email.message import EmailMessage


class MailerError(Exception):
    """面向用户的中文邮件错误；不包含 SMTP 密码等敏感信息。"""


def _get_secret(name: str, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        from dotenv import dotenv_values

        root = os.path.dirname(os.path.abspath(__file__))
        return str(dotenv_values(os.path.join(root, ".env")).get(name) or "").strip()
    except Exception:
        return default


def _smtp_port() -> int:
    raw = _get_secret("SMTP_PORT", "587") or "587"
    try:
        return int(raw)
    except ValueError:
        raise MailerError("邮件服务端口配置不正确，请联系管理员。")


def _security_mode(port: int) -> str:
    mode = (_get_secret("SMTP_SECURITY") or "").lower()
    if mode in ("ssl", "starttls", "none"):
        return mode
    return "ssl" if port == 465 else "starttls"


def _timeout() -> float:
    try:
        return float(_get_secret("SMTP_TIMEOUT", "10") or "10")
    except ValueError:
        return 10.0


def is_configured() -> bool:
    return bool(
        _get_secret("SMTP_HOST")
        and (_get_secret("SMTP_FROM") or _get_secret("SMTP_USER"))
    )


def send_verification_code(email: str, code: str) -> None:
    """
    发送 6 位验证码邮件。失败抛 MailerError（中文）；绝不把密码写进异常或日志。
    """
    host = _get_secret("SMTP_HOST")
    user = _get_secret("SMTP_USER")
    password = _get_secret("SMTP_PASSWORD")
    sender = _get_secret("SMTP_FROM") or user
    if not host or not sender:
        raise MailerError("邮件服务未配置，暂时无法发送验证码，请联系管理员。")

    port = _smtp_port()
    mode = _security_mode(port)
    message = EmailMessage()
    message["Subject"] = "课程资料问答助手 - 邮箱验证码"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        "你好，\n\n"
        f"你的注册验证码是：{code}\n\n"
        "验证码 10 分钟内有效，仅用于完成注册。\n"
        "如果这不是你本人的操作，请忽略本邮件。\n\n"
        "课程资料问答助手"
    )

    try:
        if mode == "ssl":
            client = smtplib.SMTP_SSL(
                host, port, timeout=_timeout(), context=ssl.create_default_context()
            )
        else:
            client = smtplib.SMTP(host, port, timeout=_timeout())
        with client:
            if mode == "starttls":
                client.starttls(context=ssl.create_default_context())
            if user and password:
                client.login(user, password)
            client.send_message(message)
    except MailerError:
        raise
    except Exception:
        # 不把底层异常（可能带服务器账号信息）直接回显给用户。
        raise MailerError("验证码邮件发送失败，请稍后重试或联系管理员。")
