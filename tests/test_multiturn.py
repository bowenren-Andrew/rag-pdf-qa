# -*- coding: utf-8 -*-
"""
多轮对话上下文的单元测试（v3.1 新增）

测试原则：
1. 用 unittest.mock 把 qa.OpenAI 换成假客户端，**绝不真实调用 DeepSeek API**；
2. 直接测 truncate_history 与 _build_prompt，流式用例只断言"提示词内容"，
   不依赖真实模型输出；
3. 不 import vectorstore，因此不会触发向量模型下载。
"""
from unittest.mock import MagicMock, patch

import pytest

from qa import _build_prompt, stream_answer, truncate_history

# 一份固定参考资料，保证提示词里除了历史之外的内容可控
CONTEXTS = [{"text": "访问控制列表分为基本 ACL 与高级 ACL。", "page": 3, "source": "demo.pdf"}]

# 多轮历史：三问三答，方便观察"从最旧开始丢弃"
HISTORY = [
    {"role": "user", "content": "第一问"},
    {"role": "assistant", "content": "第一答"},
    {"role": "user", "content": "第二问"},
    {"role": "assistant", "content": "第二答"},
    {"role": "user", "content": "第三问"},
    {"role": "assistant", "content": "第三答"},
]


def _fake_chunk(text):
    """构造一个流式返回块（模拟 stream=True 时的一小段文本）。"""
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = text
    return chunk


def _stream_client(pieces):
    """构造假的 OpenAI 客户端，create() 返回一段可迭代的流式响应。"""
    client = MagicMock()
    client.chat.completions.create.return_value = iter([_fake_chunk(p) for p in pieces])
    return client


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    """每个用例都注入一个假 Key，保证测试与真实 .env 解耦。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key-for-test")


# ---------------- truncate_history：截断保护 ----------------

def test_空历史返回空列表():
    """history 为 None 或 [] 时应安全返回空列表，不能报错。"""
    assert truncate_history(None) == []
    assert truncate_history([]) == []


def test_未超过上限时原样返回():
    """总字符数远小于 3000 时，一条都不该丢。"""
    result = truncate_history(HISTORY)

    assert result == HISTORY


def test_超过上限时从最旧开始丢弃():
    """超限时最旧的"第一问/第一答"应被丢掉，较新的内容保留。"""
    long_history = [
        {"role": "user", "content": "旧问题" * 500},
        {"role": "assistant", "content": "旧回答" * 500},
        {"role": "user", "content": "最近的问题"},
        {"role": "assistant", "content": "最近的回答"},
    ]
    result = truncate_history(long_history)

    assert "旧问题" not in "".join(m["content"] for m in result)
    assert result[-2]["content"] == "最近的问题"
    assert result[-1]["content"] == "最近的回答"


def test_截断后最近一轮问答必定保留():
    """无论历史多长，最后一问一答都必须在结果里。"""
    long_history = [
        {"role": "user", "content": "A" * 900},
        {"role": "assistant", "content": "B" * 900},
        {"role": "user", "content": "C" * 900},
        {"role": "user", "content": "这一轮必须留着"},
        {"role": "assistant", "content": "这一轮也必须留着"},
    ]
    result = truncate_history(long_history)

    contents = "".join(m["content"] for m in result)
    assert "这一轮必须留着" in contents
    assert "这一轮也必须留着" in contents


def test_截断后总长度不超过上限():
    """截断后的总字符数应 <= 3000（除非最近一轮本身就超过上限）。"""
    long_history = [{"role": "user", "content": "字" * 400} for _ in range(20)]
    long_history += [
        {"role": "user", "content": "最后一问"},
        {"role": "assistant", "content": "最后一答"},
    ]
    result = truncate_history(long_history)

    total = sum(len(m["content"]) for m in result)
    assert total <= 3000, f"截断后仍有 {total} 字符"


def test_历史里混入非法角色会被过滤():
    """只保留 user / assistant 两种角色，其他字段（如 sources）不参与历史。"""
    mixed = [
        {"role": "user", "content": "正常问题"},
        {"role": "system", "content": "不该进历史"},
        {"role": "assistant", "content": "正常回答", "sources": [{"text": "x"}]},
    ]
    result = truncate_history(mixed)

    assert [m["role"] for m in result] == ["user", "assistant"]
    assert "不该进历史" not in "".join(m["content"] for m in result)


# ---------------- _build_prompt：四段提示词 ----------------

def test_有历史时提示词包含对话历史段():
    """传入历史时，提示词应出现「对话历史：」以及「用户：/助手：」格式的文本。"""
    prompt = _build_prompt("新问题", CONTEXTS, HISTORY)

    assert "对话历史：" in prompt
    assert "用户：第三问" in prompt
    assert "助手：第三答" in prompt


def test_系统提示里写明历史只作背景参考():
    """系统提示必须明确：回答内容只依据本次资料，不能用历史编造。"""
    prompt = _build_prompt("新问题", CONTEXTS, HISTORY)

    assert "历史对话只作背景参考" in prompt
    assert "不要用历史内容编造" in prompt


def test_无历史时结构与原来一致():
    """回归保护：history 为空时不出现对话历史段，仍保留参考资料与用户问题。"""
    prompt = _build_prompt("新问题", CONTEXTS)

    assert "对话历史" not in prompt
    assert "参考资料：" in prompt
    assert "用户问题：新问题" in prompt
    assert "【资料1】" in prompt


def test_参考资料的编号与来源格式不变():
    """【资料N】格式与「来源 + 页码」标注要保持原样。"""
    prompt = _build_prompt("新问题", CONTEXTS)

    assert "【资料1】（来源：demo.pdf，第3页）" in prompt


# ---------------- stream_answer：历史确实传到请求里 ----------------

def test_流式回答把历史拼进了提示词():
    """stream_answer 收到的 history 应体现在发给模型的 messages 里。"""
    client = _stream_client(["回", "答"])
    with patch("qa.OpenAI", return_value=client):
        text = "".join(
            stream_answer(
                "本轮新问",
                CONTEXTS,
                history=[{"role": "user", "content": "上一轮问"}, {"role": "assistant", "content": "上一轮答"}],
                api_key="sk-fake",
            )
        )

    sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][-1]["content"]
    assert "上一轮问" in sent_prompt
    assert "上一轮答" in sent_prompt
    assert text == "回答"


def test_流式回答不传历史时提示词不含历史段():
    """不传 history 时行为与 v3 一致，不出现对话历史段。"""
    client = _stream_client(["ok"])
    with patch("qa.OpenAI", return_value=client):
        "".join(stream_answer("本轮新问", CONTEXTS, api_key="sk-fake"))

    sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][-1]["content"]
    assert "对话历史" not in sent_prompt
    assert "本轮新问" in sent_prompt


def test_超长历史会被截断后再送入请求():
    """传入超长历史时，请求里不应出现最旧的那一轮。"""
    client = _stream_client(["ok"])
    history = [
        {"role": "user", "content": "很旧的问题" * 400},
        {"role": "assistant", "content": "很旧的回答" * 400},
        {"role": "user", "content": "最近的问题"},
        {"role": "assistant", "content": "最近的回答"},
    ]
    with patch("qa.OpenAI", return_value=client):
        "".join(stream_answer("本轮新问", CONTEXTS, history=history, api_key="sk-fake"))

    sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][-1]["content"]
    assert "很旧的问题" not in sent_prompt
    assert "最近的问题" in sent_prompt
