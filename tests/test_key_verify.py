# -*- coding: utf-8 -*-
"""
API Key 轻量鉴权测试（BUG-04）

用假的 OpenAI 客户端模拟 /models 的各类返回，不发真实网络请求。
"""
import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
)

import qa

_REQUEST = httpx.Request("GET", "https://api.deepseek.com/models")


def _http_error(cls, status_code: int):
    response = httpx.Response(status_code, request=_REQUEST, json={"error": {"message": "x"}})
    return cls("boom", response=response, body=None)


def _patch_client(monkeypatch, error: Exception = None):
    """把 qa.OpenAI 换成假客户端；error 不为空时 models.list() 抛出它。"""
    calls = {}

    class _FakeModels:
        def list(self):
            calls["listed"] = True
            if error is not None:
                raise error
            return object()

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            calls.update(kwargs)
            self.models = _FakeModels()

    monkeypatch.setattr(qa, "OpenAI", _FakeOpenAI)
    return calls


def test_有效Key通过鉴权(monkeypatch):
    """鉴权成功不抛异常，且使用 /models 需要的轻量配置。"""
    calls = _patch_client(monkeypatch)

    assert qa.verify_api_key("sk-" + "a" * 32) is None
    assert calls["listed"] is True
    assert calls["max_retries"] == 0
    assert calls["base_url"] == qa.BASE_URL


def test_空Key直接拒绝(monkeypatch):
    """空 Key 不应发起网络请求。"""
    calls = _patch_client(monkeypatch)

    with pytest.raises(qa.QAFriendlyError, match="请输入"):
        qa.verify_api_key("   ")
    assert "listed" not in calls


def test_无效Key提示无效或已过期(monkeypatch):
    """/models 返回 401 时给出明确失败提示，不能显示保存成功。"""
    _patch_client(monkeypatch, _http_error(AuthenticationError, 401))

    with pytest.raises(qa.QAFriendlyError) as exc:
        qa.verify_api_key("sk-" + "a" * 32)
    assert "无效或已过期" in str(exc.value)


def test_无权限Key提示没有访问权限(monkeypatch):
    """403 归入无权限，仍要求用户重新确认 Key。"""
    _patch_client(monkeypatch, _http_error(APIStatusError, 403))

    with pytest.raises(qa.QAFriendlyError) as exc:
        qa.verify_api_key("sk-" + "a" * 32)
    assert "没有访问权限" in str(exc.value)


def test_服务端错误说明未保存(monkeypatch):
    """5xx 属于服务端问题，提示稍后重试且明确 Key 未保存。"""
    _patch_client(monkeypatch, _http_error(APIStatusError, 500))

    with pytest.raises(qa.QAFriendlyError) as exc:
        qa.verify_api_key("sk-" + "a" * 32)
    assert "HTTP 500" in str(exc.value)
    assert "Key 未保存" in str(exc.value)


def test_网络失败说明未保存(monkeypatch):
    """网络不通时不能误报 Key 无效。"""
    _patch_client(monkeypatch, APIConnectionError(request=_REQUEST))

    with pytest.raises(qa.QAFriendlyError) as exc:
        qa.verify_api_key("sk-" + "a" * 32)
    assert "无法连接" in str(exc.value)
    assert "Key 未保存" in str(exc.value)


def test_鉴权超时说明未保存(monkeypatch):
    """超时单独提示，避免用户误以为 Key 有问题。"""
    _patch_client(monkeypatch, APITimeoutError(request=_REQUEST))

    with pytest.raises(qa.QAFriendlyError) as exc:
        qa.verify_api_key("sk-" + "a" * 32)
    assert "超时" in str(exc.value)
    assert "Key 未保存" in str(exc.value)


def test_未知异常不暴露英文原文(monkeypatch):
    """未预料的异常也要转成中文提示。"""
    _patch_client(monkeypatch, RuntimeError("socket closed unexpectedly"))

    with pytest.raises(qa.QAFriendlyError) as exc:
        qa.verify_api_key("sk-" + "a" * 32)
    assert "socket closed" not in str(exc.value)
    assert "无法完成验证" in str(exc.value)
