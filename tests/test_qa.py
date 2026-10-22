# -*- coding: utf-8 -*-
"""
qa.py 的单元测试（JSON 解析 + 错误处理 + 流式输出）

测试原则：
1. 用 unittest.mock 把 qa.OpenAI 换成假客户端，**绝不真实调用 DeepSeek API**；
2. 用 monkeypatch 注入假的 API Key，不依赖真实 .env；
3. 不 import vectorstore，因此不会触发向量模型下载。
"""
from unittest.mock import MagicMock, patch

import pytest
from openai import APIConnectionError, AuthenticationError

from qa import QAFriendlyError, generate_doc_brief, get_api_key, stream_answer


# ---------------- 测试辅助工具 ----------------

def _make_client(content: str = None, create_side_effect=None):
    """
    构造假的 OpenAI 客户端，替代 qa.OpenAI。
    :param content: 让 create() 返回这段文本（模拟模型输出的 JSON）
    :param create_side_effect: 让 create() 抛出这个异常（模拟网络/鉴权失败）
    """
    client = MagicMock()
    if create_side_effect is not None:
        client.chat.completions.create.side_effect = create_side_effect
    else:
        choice = MagicMock()
        choice.message.content = content
        resp = MagicMock()
        resp.choices = [choice]
        client.chat.completions.create.return_value = resp
    return client


def _fake_chunk(text):
    """构造一个流式返回块（模拟 stream=True 时的一小段文本）。"""
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = text
    return chunk


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    """每个用例都注入一个假 Key，保证测试与真实 .env 解耦。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key-for-test")


# ---------------- get_api_key ----------------

def test_get_api_key_能读到环境变量中的Key(monkeypatch):
    """验证 Key 是从环境变量读取的，而不是硬编码在代码里。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-unit-test-key")
    assert get_api_key() == "sk-unit-test-key"


# ---------------- generate_doc_brief：JSON 解析 ----------------

def test_解析标准JSON得到摘要和三个推荐问题():
    """最理想情况：模型返回合法 JSON，应正确拆出 summary 和 questions。"""
    content = '{"summary": "这是全文摘要", "questions": ["问题1", "问题2", "问题3"]}'
    with patch("qa.OpenAI", return_value=_make_client(content)):
        brief = generate_doc_brief("demo.pdf", [{"text": "片段内容", "page": 1, "source": "demo.pdf"}])

    assert brief["summary"] == "这是全文摘要"
    assert brief["questions"] == ["问题1", "问题2", "问题3"]


def test_模型把JSON包在代码块里也能解析():
    """模型经常'好心'地加 ```json 围栏，必须能容错解析。"""
    content = '```json\n{"summary": "摘要A", "questions": ["A", "B", "C"]}\n```'
    with patch("qa.OpenAI", return_value=_make_client(content)):
        brief = generate_doc_brief("demo.pdf", [{"text": "片段内容", "page": 1, "source": "demo.pdf"}])

    assert brief["summary"] == "摘要A"
    assert brief["questions"] == ["A", "B", "C"]


def test_JSON非法时降级而不会抛异常():
    """模型返回非 JSON 时，降级为'原文当摘要、问题留空'，流程不中断。"""
    content = "抱歉，我无法按要求输出 JSON。"
    with patch("qa.OpenAI", return_value=_make_client(content)):
        brief = generate_doc_brief("demo.pdf", [{"text": "片段内容", "page": 1, "source": "demo.pdf"}])

    assert brief["summary"] == content     # 原文兜底展示
    assert brief["questions"] == []        # 不强行造问题


def test_推荐问题超过三条时只保留前三条():
    """模型可能给 5 条，界面只设计了 3 个按钮，必须截断。"""
    content = '{"summary": "摘要", "questions": ["1", "2", "3", "4", "5"]}'
    with patch("qa.OpenAI", return_value=_make_client(content)):
        brief = generate_doc_brief("demo.pdf", [{"text": "片段内容", "page": 1, "source": "demo.pdf"}])

    assert len(brief["questions"]) == 3
    assert brief["questions"] == ["1", "2", "3"]


def test_发送给模型的提示词包含文件名页码和片段原文():
    """摘要提示词必须带上文档名、页码和原文，模型才知道要总结什么。"""
    fake = _make_client('{"summary": "摘要", "questions": ["A"]}')
    with patch("qa.OpenAI", return_value=fake):
        generate_doc_brief("demo.pdf", [{"text": "独一无二的片段内容", "page": 7}])

    prompt = fake.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "demo.pdf" in prompt
    assert "第7页" in prompt
    assert "独一无二的片段内容" in prompt


# ---------------- generate_doc_brief：错误处理 ----------------

def test_缺少APIKey时给出中文提示而不是崩溃(monkeypatch):
    """未配置 Key 时应抛出带中文指引的 QAFriendlyError。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    with pytest.raises(QAFriendlyError, match="未检测到 API Key"):
        generate_doc_brief("demo.pdf", [{"text": "片段内容", "page": 1, "source": "demo.pdf"}])


def test_鉴权失败时提示检查Key():
    """Key 无效（HTTP 401）应被翻译成'Key 无效或已过期'。"""
    resp = MagicMock(status_code=401)
    err = AuthenticationError("Invalid API key", response=resp, body=None)
    with patch("qa.OpenAI", return_value=_make_client(create_side_effect=err)):
        with pytest.raises(QAFriendlyError, match="无效或已过期"):
            generate_doc_brief("demo.pdf", [{"text": "片段内容", "page": 1, "source": "demo.pdf"}])


def test_连接失败时提示检查网络():
    """网络不通应被翻译成'无法连接 DeepSeek API'。"""
    err = APIConnectionError(message="connection failed", request=MagicMock())
    with patch("qa.OpenAI", return_value=_make_client(create_side_effect=err)):
        with pytest.raises(QAFriendlyError, match="无法连接"):
            generate_doc_brief("demo.pdf", [{"text": "片段内容", "page": 1, "source": "demo.pdf"}])


def test_其他未知异常也被兜底成中文提示():
    """兜底分支：任何未预料异常都不能让程序崩溃。"""
    with patch("qa.OpenAI", return_value=_make_client(create_side_effect=RuntimeError("boom"))):
        with pytest.raises(QAFriendlyError, match="调用 DeepSeek API 失败"):
            generate_doc_brief("demo.pdf", [{"text": "片段内容", "page": 1, "source": "demo.pdf"}])


# ---------------- stream_answer：流式输出 ----------------

def test_流式输出把多个chunk拼接成完整回答():
    """生成器应逐段 yield 文本，拼接后与模型输出一致。"""
    fake = _make_client()
    empty_choices = MagicMock()
    empty_choices.choices = []                       # 模拟流末尾的 usage 块
    none_delta = _fake_chunk(None)                   # 模拟 delta 为 None 的块
    fake.chat.completions.create.return_value = [
        _fake_chunk("你好"),
        _fake_chunk("，"),
        none_delta,
        _fake_chunk("世界"),
        empty_choices,
    ]

    with patch("qa.OpenAI", return_value=fake):
        pieces = list(stream_answer("问题？", [{"text": "片段内容", "page": 1, "source": "demo.pdf"}]))

    assert "".join(pieces) == "你好，世界"     # 空 choices 与 None delta 都被正确跳过


def test_流式请求确实开启了stream参数():
    """必须传 stream=True，否则不会有流式效果。"""
    fake = _make_client()
    fake.chat.completions.create.return_value = [_fake_chunk("A")]
    with patch("qa.OpenAI", return_value=fake):
        list(stream_answer("问题？", [{"text": "片段内容", "page": 1, "source": "demo.pdf"}]))

    assert fake.chat.completions.create.call_args.kwargs["stream"] is True


def test_没有检索结果时给出友好提示():
    """检索为空时不该白屏，而应抛中文提示交给界面展示。"""
    with pytest.raises(QAFriendlyError, match="没有检索到相关内容"):
        list(stream_answer("问题？", []))
