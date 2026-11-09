# -*- coding: utf-8 -*-
"""P3：模型用量采集与费用估算的单元测试（不发起真实网络请求）。"""
import datetime as dt
from unittest.mock import MagicMock, patch

from qa import (
    BEIJING_TZ,
    QAFriendlyError,
    estimate_cost,
    format_usage,
    generate_answer,
    generate_doc_brief,
    is_peak_time,
    stream_answer,
    usage_to_dict,
)

CONTEXTS = [{"text": "片段内容", "page": 1, "source": "demo.pdf"}]


class _Usage:
    """模拟 SDK 的 usage 对象（属性形式）。"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):
        return self.__dict__.get(name)


def _client(content=None):
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    client.chat.completions.create.return_value = resp
    return client


def _chunk(text=None, usage=None, model=None):
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = text
    chunk.usage = usage
    chunk.model = model
    return chunk


# ---------------- usage_to_dict ----------------

def test_usage_to_dict_保留缓存命中与未命中():
    usage = _Usage(
        prompt_tokens=1000,
        completion_tokens=200,
        total_tokens=1200,
        prompt_cache_hit_tokens=400,
        prompt_cache_miss_tokens=600,
    )
    result = usage_to_dict(usage, "deepseek-flash")
    assert result["model"] == "deepseek-flash"
    assert result["prompt_tokens"] == 1000
    assert result["completion_tokens"] == 200
    assert result["total_tokens"] == 1200
    assert result["prompt_cache_hit_tokens"] == 400
    assert result["prompt_cache_miss_tokens"] == 600


def test_usage_to_dict_只有prompt_tokens_details时也能读缓存命中():
    usage = _Usage(
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        prompt_tokens_details=_Usage(cached_tokens=30),
    )
    result = usage_to_dict(usage)
    assert result["prompt_cache_hit_tokens"] == 30
    assert result["prompt_cache_miss_tokens"] == 70
    assert result["model"] == "deepseek-flash"


def test_usage_to_dict_缺字段时给0():
    result = usage_to_dict(_Usage(prompt_tokens=5, completion_tokens=2, total_tokens=7))
    assert result["prompt_cache_hit_tokens"] is None
    assert result["prompt_cache_miss_tokens"] is None


# ---------------- 高峰时段 ----------------

def test_高峰时段判断():
    assert is_peak_time(dt.datetime(2026, 11, 6, 10, 0, tzinfo=BEIJING_TZ)) is True
    assert is_peak_time(dt.datetime(2026, 11, 6, 14, 0, tzinfo=BEIJING_TZ)) is True
    assert is_peak_time(dt.datetime(2026, 11, 6, 12, 0, tzinfo=BEIJING_TZ)) is False
    assert is_peak_time(dt.datetime(2026, 11, 6, 13, 59, tzinfo=BEIJING_TZ)) is False
    assert is_peak_time(dt.datetime(2026, 11, 7, 10, 0, tzinfo=BEIJING_TZ)) is False


def test_无时区时间按北京时间处理():
    assert is_peak_time(dt.datetime(2026, 11, 6, 10, 0)) is True


# ---------------- 费用估算 ----------------

def test_flash高峰价按官方单价计算():
    usage = {
        "model": "deepseek-flash",
        "prompt_tokens": 1_000_000,
        "completion_tokens": 1_000_000,
        "total_tokens": 2_000_000,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 1_000_000,
    }
    peak = dt.datetime(2026, 11, 6, 10, 0, tzinfo=BEIJING_TZ)
    assert estimate_cost(usage, now=peak) == 10.0  # 输入 2 + 输出 8


def test_flash空闲价是高峰价的一半():
    usage = {
        "model": "deepseek-flash",
        "prompt_tokens": 1_000_000,
        "completion_tokens": 1_000_000,
        "total_tokens": 2_000_000,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 1_000_000,
    }
    offpeak = dt.datetime(2026, 11, 7, 10, 0, tzinfo=BEIJING_TZ)
    assert estimate_cost(usage, now=offpeak) == 5.0


def test_pro模型自动切换到pro价目():
    usage = {
        "model": "deepseek-v4-pro",
        "prompt_tokens": 1_000_000,
        "completion_tokens": 1_000_000,
        "total_tokens": 2_000_000,
    }
    peak = dt.datetime(2026, 11, 6, 10, 0, tzinfo=BEIJING_TZ)
    assert estimate_cost(usage, now=peak) == 36.0  # 9 + 27


def test_缓存命中按命中价计算():
    usage = {
        "model": "deepseek-flash",
        "prompt_tokens": 1_000_000,
        "completion_tokens": 0,
        "total_tokens": 1_000_000,
        "prompt_cache_hit_tokens": 1_000_000,
        "prompt_cache_miss_tokens": 0,
    }
    peak = dt.datetime(2026, 11, 6, 10, 0, tzinfo=BEIJING_TZ)
    assert estimate_cost(usage, now=peak) == 0.04


# ---------------- 展示文案 ----------------

def test_format_usage包含模型用量与估算费用():
    usage = {
        "model": "deepseek-flash",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "total_tokens": 1500,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 1000,
    }
    peak = dt.datetime(2026, 11, 6, 10, 0, tzinfo=BEIJING_TZ)
    text = format_usage(usage, now=peak)
    assert "deepseek-flash" in text
    assert "1,000" in text
    assert "500" in text
    assert "¥0.0060" in text
    assert "估算" in text


def test_format_usage没有用量时返回空字符串():
    assert format_usage({}) == ""
    assert format_usage({"total_tokens": 0}) == ""


# ---------------- stream_answer ----------------

def test_流式请求显式开启include_usage():
    fake = _client()
    fake.chat.completions.create.return_value = []
    with patch("qa.OpenAI", return_value=fake):
        list(stream_answer("问题？", CONTEXTS, api_key="sk-fake", usage_out={}))

    kwargs = fake.chat.completions.create.call_args.kwargs
    assert kwargs["stream_options"] == {"include_usage": True}
    assert kwargs["stream"] is True


def test_流式回答采集usage且不混入正文():
    usage = _Usage(
        prompt_tokens=120,
        completion_tokens=30,
        total_tokens=150,
        prompt_cache_hit_tokens=20,
        prompt_cache_miss_tokens=100,
    )
    fake = _client()
    fake.chat.completions.create.return_value = [
        _chunk("你好", model="deepseek-flash"),
        _chunk(None, usage=usage, model="deepseek-flash"),
    ]
    out = {}
    with patch("qa.OpenAI", return_value=fake):
        pieces = list(
            stream_answer("问题？", CONTEXTS, api_key="sk-fake", usage_out=out)
        )

    assert "".join(pieces) == "你好"
    assert out["total_tokens"] == 150
    assert out["prompt_cache_hit_tokens"] == 20
    assert out["model"] == "deepseek-flash"


def test_流式失败仍抛中文友好错误():
    from openai import APIConnectionError

    fake = _client()
    fake.chat.completions.create.side_effect = APIConnectionError(
        message="boom", request=MagicMock()
    )
    with patch("qa.OpenAI", return_value=fake):
        try:
            list(stream_answer("问题？", CONTEXTS, api_key="sk-fake", usage_out={}))
        except QAFriendlyError as e:
            assert "无法连接" in str(e)
        else:
            raise AssertionError("应当抛出 QAFriendlyError")


# ---------------- 非流式调用 ----------------

def test_generate_answer采集usage():
    fake = _client("回答")
    fake.chat.completions.create.return_value.usage = _Usage(
        prompt_tokens=80, completion_tokens=20, total_tokens=100
    )
    fake.chat.completions.create.return_value.model = "deepseek-flash"
    out = {}
    with patch("qa.OpenAI", return_value=fake):
        assert generate_answer("问题？", CONTEXTS, api_key="sk-fake", usage_out=out) == "回答"

    assert out["total_tokens"] == 100
    assert out["model"] == "deepseek-flash"


def test_generate_doc_brief采集usage():
    fake = _client('{"summary": "摘要", "questions": ["A"]}')
    fake.chat.completions.create.return_value.usage = _Usage(
        prompt_tokens=200, completion_tokens=50, total_tokens=250
    )
    fake.chat.completions.create.return_value.model = "deepseek-flash"
    out = {}
    with patch("qa.OpenAI", return_value=fake):
        brief = generate_doc_brief("demo.pdf", CONTEXTS, api_key="sk-fake", usage_out=out)

    assert brief["summary"] == "摘要"
    assert out["total_tokens"] == 250
    assert out["model"] == "deepseek-flash"
