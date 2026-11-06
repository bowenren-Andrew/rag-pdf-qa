# -*- coding: utf-8 -*-
"""
v3.5 安全加固（P3-1 / P3-2 / P3-3）的单元与界面测试。

- P3-1：恶意文件名在 Markdown 渲染点只显示为纯文本，不生成链接/图片；
- P3-2：保存或删除 Key 后，设置页输入框被清空；
- P3-3：异常日志与上游错误文案里的 sk- 明文被脱敏。
"""
import logging
import os
import re

import httpx
import pytest
from openai import APIStatusError
from streamlit.testing.v1 import AppTest

import auth
import db
import qa
from ui_helpers import escape_markdown

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)

MALICIOUS_NAME = (
    "[CLICK](http://evil.example/x)"
    "![IMG](http://evil.example/a.png)"
    "<script>alert(1)</script>"
)
FAKE_KEY_A = "sk-" + "a" * 32
FAKE_KEY_B = "sk-" + "b" * 32
FAKE_KEY_C = "sk-" + "c" * 32
LEAKED_KEY = "sk-AUDIT1234567890ABCDEFGHIJKLMN"


def _raw_markdown_link(text: str):
    """返回未被转义的 Markdown 链接/图片语法；有命中说明可能被渲染成链接。"""
    return re.search(r"(?<!\\)\]\(", text)


def _new_user(tmp_db, username: str) -> dict:
    return db.create_user(username, auth.hash_password("Password123"))


def _switch_page(at: AppTest, page_name: str) -> AppTest:
    page_hash = next(
        h for h, info in at._registered_pages.items() if info["page_name"] == page_name
    )
    at._page_hash = page_hash
    at.run()
    return at


def _open_page(user: dict, page_name: str, messages=None):
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["user"] = {"id": user["id"], "username": user["username"]}
    if messages is not None:
        # 预先写入会话状态，避免 app.py 初始化时把 messages 重置为空
        at.session_state["session_id"] = None
        at.session_state["session_title"] = "新会话"
        at.session_state["messages"] = messages
        at.session_state["doc_briefs"] = {}
        at.session_state["pending_question"] = None
        at.session_state["session_doc_ids"] = []
        at.session_state["pending_doc_ids"] = []
        at.session_state["session_scope_names"] = []
    at.run()
    return _switch_page(at, page_name)


def _seed_document(user_id: int, filename: str) -> int:
    doc_id = db.create_document(user_id, filename)
    db.insert_chunks(
        user_id,
        doc_id,
        [{"page": 1, "text": "片段内容", "embedding": b"\x00" * 8, "vector": None}],
    )
    return doc_id


def _key_input(at: AppTest):
    return next(t for t in at.text_input if t.label == "填入新的 Key")


# ---------------- P3-1：文件名 Markdown 注入 ----------------
def test_escape_markdown_中和链接与图片():
    assert (
        escape_markdown("[CLICK](http://evil.example)")
        == r"\[CLICK\]\(http://evil.example\)"
    )
    assert (
        escape_markdown("![IMG](http://evil.example/a.png)")
        == r"\!\[IMG\]\(http://evil.example/a.png\)"
    )


def test_escape_markdown_中和html标签():
    escaped = escape_markdown("<script>alert(1)</script>")
    assert escaped == r"\<script\>alert\(1\)\</script\>"
    assert "<script>" not in escaped


def test_escape_markdown_正常文件名显示内容不变():
    escaped = escape_markdown("qa-report_v2.pdf")
    assert escaped == r"qa-report\_v2.pdf"
    assert escaped.replace("\\", "") == "qa-report_v2.pdf"


def test_恶意文件名在文档列表与预览中只显示为纯文本(tmp_db):
    user = _new_user(tmp_db, "sec_doc_md")
    _seed_document(user["id"], MALICIOUS_NAME)

    at = _open_page(user, "我的文档")

    body = "\n".join(m.value for m in at.markdown)
    labels = "\n".join(e.label for e in at.expander)
    combined = body + "\n" + labels
    assert escape_markdown(MALICIOUS_NAME) in combined
    assert r"\<script\>" in combined
    assert not _raw_markdown_link(body)
    assert not _raw_markdown_link(labels)


def test_恶意文件名在删除确认框里只显示为纯文本(tmp_db):
    user = _new_user(tmp_db, "sec_doc_delete_md")
    _seed_document(user["id"], MALICIOUS_NAME)
    at = _open_page(user, "我的文档")

    next(b for b in at.button if b.label == "删除").click().run()

    body = "\n".join(m.value for m in at.markdown)
    assert "确认删除" in [b.label for b in at.button]
    assert escape_markdown(MALICIOUS_NAME) in body
    assert not _raw_markdown_link(body)


def test_引用来源的文件名只显示为纯文本(tmp_db):
    user = _new_user(tmp_db, "sec_source_md")
    messages = [
        {
            "role": "assistant",
            "content": "回答内容",
            "sources": [
                {
                    "source": MALICIOUS_NAME,
                    "page": 1,
                    "score": 0.2,
                    "text": "片段",
                }
            ],
        }
    ]
    at = _open_page(user, "问答", messages=messages)

    body = "\n".join(m.value for m in at.markdown)
    assert escape_markdown(MALICIOUS_NAME) in body
    assert not _raw_markdown_link(body)


# ---------------- P3-2：保存/删除后清空 Key 输入框 ----------------
def test_保存Key成功后输入框清空(tmp_db, fernet_key, monkeypatch):
    class _FakeModels:
        def list(self):
            return {"data": []}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.models = _FakeModels()

    monkeypatch.setattr(qa, "OpenAI", _FakeOpenAI)
    user = _new_user(tmp_db, "sec_key_clear_save")
    at = _open_page(user, "设置")

    _key_input(at).set_value(FAKE_KEY_A)
    next(b for b in at.button if b.label == "保存 Key").click().run()

    assert _key_input(at).value in ("", None)
    assert at.session_state["_api_key_input_version"] == 1
    assert "new_api_key_input_0" not in at.session_state
    assert db.get_user_by_id(user["id"])["api_key_enc"]


def test_删除Key后输入框清空(tmp_db, fernet_key):
    user = _new_user(tmp_db, "sec_key_clear_delete")
    auth.set_user_api_key(user["id"], FAKE_KEY_B)
    at = _open_page(user, "设置")

    _key_input(at).set_value(FAKE_KEY_C).run()
    next(b for b in at.button if b.label == "删除已保存的 Key").click().run()

    assert _key_input(at).value in ("", None)
    assert at.session_state["_api_key_input_version"] == 1
    assert "new_api_key_input_0" not in at.session_state
    assert db.get_user_by_id(user["id"])["api_key_enc"] is None


# ---------------- P3-3：日志与错误文案脱敏 ----------------
def test_redact_secrets_替换密钥且不误伤普通单词():
    text = f"Bearer {LEAKED_KEY} rejected; risks-management task-list"
    out = qa.redact_secrets(text)

    assert "AUDIT1234" not in out
    assert "sk-****" in out
    assert "risks-management" in out
    assert "task-list" in out


def test_friendly错误提示脱敏上游消息():
    response = httpx.Response(
        500,
        request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"),
    )
    err = APIStatusError(f"upstream failed for {LEAKED_KEY}", response=response, body=None)

    message = str(qa._friendly(err))

    assert "AUDIT1234" not in message
    assert "sk-****" in message
    assert "HTTP 500" in message


def test_friendly错误提示脱敏普通异常():
    message = str(qa._friendly(RuntimeError(f"boom {LEAKED_KEY}")))

    assert "AUDIT1234" not in message
    assert "sk-****" in message


def test_校验Key日志不写明文(monkeypatch, caplog):
    class _BoomClient:
        def __init__(self, **kwargs):
            self.models = self

        def list(self):
            raise RuntimeError(f"rejected {LEAKED_KEY}")

    monkeypatch.setattr(qa, "OpenAI", _BoomClient)

    with caplog.at_level(logging.WARNING, logger="qa"):
        with pytest.raises(qa.QAFriendlyError):
            qa.verify_api_key(FAKE_KEY_A)

    assert "AUDIT1234" not in caplog.text
    assert "sk-****" in caplog.text
