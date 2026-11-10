# -*- coding: utf-8 -*-
"""
turnstile.py（v3.9 防刷注册）
职责：Cloudflare Turnstile 服务端校验。Secret Key 只从环境变量 / Secrets 读取，
绝不写进代码、页面或日志。
"""
import json
import os
import urllib.parse
import urllib.request

DEFAULT_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
FAIL_MESSAGE = "请完成人机验证"


def _get_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        from dotenv import dotenv_values

        root = os.path.dirname(os.path.abspath(__file__))
        return str(dotenv_values(os.path.join(root, ".env")).get(name) or "").strip()
    except Exception:
        return ""


def site_key() -> str:
    """公开的 Turnstile Site Key（可以出现在页面里）。"""
    return _get_secret("TURNSTILE_SITE_KEY")


def verify_token(token: str, remote_ip: str | None = None) -> tuple[bool, str]:
    """
    服务端校验 Turnstile token。
    返回 (是否通过, 中文提示)。网络异常与校验失败都给中文，不暴露 Cloudflare 英文错误码。
    """
    token = (token or "").strip()
    if not token:
        return False, FAIL_MESSAGE
    secret = _get_secret("TURNSTILE_SECRET_KEY")
    if not secret:
        return False, "服务端未配置人机验证，请稍后联系管理员。"

    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    data = urllib.parse.urlencode(payload).encode("utf-8")
    url = _get_secret("TURNSTILE_VERIFY_URL") or DEFAULT_VERIFY_URL
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
    except Exception:
        return False, "人机验证服务暂时不可用，请稍后重试。"
    if body.get("success") is True:
        return True, ""
    return False, FAIL_MESSAGE
