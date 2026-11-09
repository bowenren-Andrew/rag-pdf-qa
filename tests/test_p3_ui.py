# -*- coding: utf-8 -*-
"""P3：问答等待反馈、模型/用量展示的界面级测试（AppTest，不发起真实网络请求）。"""
import os
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import auth
import db
from qa import QAFriendlyError

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)

USAGE = {
    "model": "deepseek-flash",
    "prompt_tokens": 1000,
    "completion_tokens": 200,
    "total_tokens": 1200,
    "prompt_cache_hit_tokens": 400,
    "prompt_cache_miss_tokens": 600,
}


def _open_chat(tmp_db, username: str, with_key: bool = True):
    user = db.create_user(username, auth.hash_password("Password123"))
    if with_key:
        auth.set_user_api_key(user["id"], "sk-1234567890abcdefghij")
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["user"] = {"id": user["id"], "username": username}
    at.run()
    page_hash = next(
        h for h, info in at._registered_pages.items() if info["page_name"] == "问答"
    )
    at._page_hash = page_hash
    at.run()
    return at


def _send(at, question: str):
    at.text_area[0].set_value(question).run()
    next(b for b in at.button if b.label == "发送").click().run()


def _body(at):
    return " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)


def _success_stream(*args, **kwargs):
    usage_out = kwargs.get("usage_out")
    if usage_out is not None:
        usage_out.update(USAGE)
    yield "这是"
    yield "回答"


def _fail_stream(*args, **kwargs):
    raise QAFriendlyError(
        "你填写的 DeepSeek API Key 无效或已过期，请到「⚙️ 设置」页更新。"
    )
    yield ""  # pragma: no cover


def test_问答页显示当前模型名(tmp_db, fernet_key):
    at = _open_chat(tmp_db, "p3_model")

    assert any("回答模型：deepseek-flash" in c.value for c in at.caption)


def test_回答后显示token用量与估算费用且不残留思考中(tmp_db, fernet_key):
    at = _open_chat(tmp_db, "p3_usage_ok")

    with patch("qa.stream_answer", side_effect=_success_stream), patch(
        "vectorstore.similarity_search", return_value=[]
    ):
        _send(at, "电池更换多少钱？")

    body = _body(at)
    assert "这是回答" in body
    assert "估算费用" in body
    assert "deepseek-flash" in body
    assert "1,200" not in body  # 不展示 total，避免和输入/输出混淆
    assert "思考中" not in body


def test_流式失败显示中文错误且不残留思考中(tmp_db, fernet_key):
    at = _open_chat(tmp_db, "p3_usage_fail")

    with patch("qa.stream_answer", side_effect=_fail_stream), patch(
        "vectorstore.similarity_search", return_value=[]
    ):
        _send(at, "这个问题会失败")

    assert any("API Key 无效或已过期" in w.value for w in at.warning)
    body = _body(at)
    assert "思考中" not in body
    assert "估算费用" not in body


def test_没有用户Key时自动使用平台Key(tmp_db, fernet_key, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-platform-test-1234567890")
    at = _open_chat(tmp_db, "p3_no_key", with_key=False)

    with patch("qa.stream_answer", side_effect=_success_stream), patch(
        "vectorstore.similarity_search", return_value=[]
    ):
        _send(at, "没有自填 Key 的提问")

    assert not any("平台 Key 暂未配置" in w.value for w in at.warning)
    body = _body(at)
    assert "这是回答" in body
    assert "估算费用" in body


def test_摘要用量在问答页可见(tmp_db, fernet_key):
    at = _open_chat(tmp_db, "p3_brief_usage")
    at.session_state["doc_briefs"] = {
        "demo.pdf": {"summary": "摘要内容", "questions": [], "usage": USAGE}
    }
    at.session_state["session_scope_names"] = ["demo.pdf"]
    at.run()

    assert any(
        "摘要生成" in c.value and "估算费用" in c.value for c in at.caption
    )


def test_首屏加载骨架不会残留(tmp_db, fernet_key):
    """骨架屏只在首次加载阶段显示，脚本跑完后必须清掉且不再重复出现。"""
    at = _open_chat(tmp_db, "p3_boot")

    assert "正在加载课程资料问答助手" not in _body(at)
    assert at.session_state["app_booted"] is True


def _capture_answer(captured):
    def _stream(*args, **kwargs):
        captured["api_key"] = kwargs.get("api_key")
        yield "这是回答"

    return _stream


def test_老用户自填Key优先于平台Key(tmp_db, fernet_key, monkeypatch):
    """老用户存过自己的 Key 时继续用他的 Key，平台 Key 只做兜底。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-platform-test-1234567890")
    at = _open_chat(tmp_db, "p3_legacy_key", with_key=True)
    captured = {}

    with patch("qa.stream_answer", side_effect=_capture_answer(captured)), patch(
        "vectorstore.similarity_search", return_value=[]
    ):
        _send(at, "老用户用自己的 Key")

    assert captured["api_key"] == "sk-1234567890abcdefghij"
    assert "这是回答" in _body(at)


def test_未自填Key时把平台Key传给模型(tmp_db, fernet_key, monkeypatch):
    """没填 Key 的新用户走 Streamlit Secrets 里的平台 Key，不需要自备 Key。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-platform-test-1234567890")
    at = _open_chat(tmp_db, "p3_platform_key", with_key=False)
    captured = {}

    with patch("qa.stream_answer", side_effect=_capture_answer(captured)), patch(
        "vectorstore.similarity_search", return_value=[]
    ):
        _send(at, "新用户直接用平台 Key")

    assert captured["api_key"] == "sk-platform-test-1234567890"
    assert "这是回答" in _body(at)
