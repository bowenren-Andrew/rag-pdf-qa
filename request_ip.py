# -*- coding: utf-8 -*-
"""
request_ip.py（v3.9 防刷注册）
职责：从 Streamlit 提供的上下文里取真实客户端 IP，并转成不可逆的计数键。

取 IP 的优先级：
1. st.context.ip_address（Streamlit 1.66+ 直接提供的客户端地址）；
2. 反向代理头 X-Forwarded-For / X-Real-IP / CF-Connecting-IP（仅在上一步拿不到
   可用地址时兜底）。

本机回环地址（127.0.0.1、::1）和 unspecified 地址不会参与计数，避免本地开发时
所有请求被算成同一个“真实 IP”。拿不到可用 IP 时返回 None，调用方只按账号额度处理。
"""
import hashlib
import ipaddress


def _normalize_candidate(value: str) -> str:
    """去掉端口、方括号和首尾空格，返回可用于 ip_address() 的字符串。"""
    value = (value or "").strip()
    if not value:
        return ""
    # X-Forwarded-For 可能是 "1.2.3.4, 5.6.7.8"，只取第一个
    value = value.split(",")[0].strip()
    if value.startswith("[") and "]" in value:
        return value[1 : value.index("]")]
    if value.count(":") == 1 and "." in value:
        host, port = value.rsplit(":", 1)
        if port.isdigit():
            return host
    return value


def normalize_ip(value: str) -> str | None:
    """校验并规范化 IP；回环 / unspecified / 非法值一律返回 None。"""
    candidate = _normalize_candidate(value)
    if not candidate:
        return None
    try:
        parsed = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if parsed.is_loopback or parsed.is_unspecified:
        return None
    return str(parsed)


def _from_streamlit_context() -> str | None:
    """读取 st.context.ip_address；在非 Streamlit 环境/测试中返回 None。"""
    try:
        import streamlit as st

        return normalize_ip(getattr(st.context, "ip_address", "") or "")
    except Exception:
        return None


def _from_headers() -> str | None:
    """反向代理头兜底；头部名大小写不敏感。"""
    try:
        import streamlit as st

        headers = st.context.headers
    except Exception:
        return None
    if not headers:
        return None
    lowered = {str(k).lower(): v for k, v in dict(headers).items()}
    for name in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        ip = normalize_ip(lowered.get(name, ""))
        if ip:
            return ip
    return None


def get_client_ip() -> str | None:
    """返回当前请求的真实客户端 IP；拿不到合法地址时返回 None。"""
    return _from_streamlit_context() or _from_headers()


def hash_ip(ip: str | None) -> str:
    """
    把 IP 转成稳定的 64 位十六进制计数键（sha256）。
    只存哈希，不把原始 IP 写进数据库。
    """
    normalized = normalize_ip(ip) if ip else None
    if not normalized:
        return ""
    return hashlib.sha256(f"client-ip:{normalized}".encode("utf-8")).hexdigest()
