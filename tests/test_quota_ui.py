# -*- coding: utf-8 -*-
"""免费额度在真实 app.py 中的界面级验证（AppTest，不发起真实网络请求）。"""
import io
import os
from unittest.mock import patch

from PyPDF2 import PdfWriter
from streamlit.testing.v1 import AppTest

import auth
import db
import quota

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)


def _open_chat(tmp_db, username: str):
    user = db.create_user(username, auth.hash_password("Password123"))
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["user"] = {"id": user["id"], "username": username}
    at.run()
    page_hash = next(
        h for h, info in at._registered_pages.items() if info["page_name"] == "问答"
    )
    at._page_hash = page_hash
    at.run()
    return at, user


def _send(at, question: str):
    at.text_area[0].set_value(question).run()
    next(b for b in at.button if b.label == "发送").click().run()


def _body(at) -> str:
    return " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)


def _pdf_bytes(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _open_documents(tmp_db, username: str):
    user = db.create_user(username, auth.hash_password("Password123"))
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["user"] = {"id": user["id"], "username": username}
    at.run()
    page_hash = next(
        h for h, info in at._registered_pages.items() if info["page_name"] == "我的文档"
    )
    at._page_hash = page_hash
    at.run()
    return at, user


def _upload_and_parse(at, filename: str, content: bytes):
    at.file_uploader[0].set_value((filename, content, "application/pdf")).run()
    next(b for b in at.button if b.label == "解析并入库").click().run()


def test_免费账号第6次提问被拦且不再调API(tmp_db, app_secret, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-platform-test-1234567890")
    at, user = _open_chat(tmp_db, "quota_ui_six")
    calls = {"n": 0}

    def _ok_stream(*args, **kwargs):
        calls["n"] += 1
        usage_out = kwargs.get("usage_out")
        if usage_out is not None:
            usage_out.update({"model": "deepseek-flash", "total_tokens": 10})
        yield "回答"

    with patch("qa.stream_answer", side_effect=_ok_stream), patch(
        "vectorstore.similarity_search", return_value=[]
    ):
        for i in range(5):
            _send(at, f"第 {i + 1} 个问题")
        _send(at, "第 6 个问题")

    assert calls["n"] == 5
    assert db.get_daily_usage(user["id"], db_date())["question_count"] == 5
    assert any("今日提问次数已用完" in w.value for w in at.warning)
    msg = next(w.value for w in at.warning if "今日提问次数已用完" in w.value)
    assert "明天 0 点" in msg
    assert "9.9/月" in msg


def test_顶部额度随使用减少(tmp_db, app_secret, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-platform-test-1234567890")
    at, _ = _open_chat(tmp_db, "quota_ui_topbar")
    assert "今日还剩 5 问 / 还可存 3 个文档" in _body(at)

    def _ok_stream(*args, **kwargs):
        yield "回答"

    with patch("qa.stream_answer", side_effect=_ok_stream), patch(
        "vectorstore.similarity_search", return_value=[]
    ):
        _send(at, "第一个问题")

    assert "今日还剩 4 问 / 还可存 3 个文档" in _body(at)


def test_101页文件在文档页被拦并提示拆分(tmp_db, app_secret, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-platform-test-1234567890")
    at, _ = _open_documents(tmp_db, "quota_ui_101")

    with patch("loader.load_and_split_file") as parse:
        _upload_and_parse(at, "book.pdf", _pdf_bytes(101))

    parse.assert_not_called()
    assert any("文件过大（超过 100 页或 20MB）" in e.value for e in at.error)
    assert any("请按章节拆分后上传" in e.value for e in at.error)


def test_免费账号存满3个文档后再传被拦(tmp_db, app_secret, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-platform-test-1234567890")
    at, user = _open_documents(tmp_db, "quota_ui_docs_full")
    for i in range(3):
        db.create_document(user["id"], f"old{i}.pdf")

    with patch("loader.load_and_split_file") as parse:
        _upload_and_parse(at, "fourth.pdf", _pdf_bytes(1))

    parse.assert_not_called()
    assert any("文档存储已满" in w.value for w in at.warning)
    msg = next(w.value for w in at.warning if "文档存储已满" in w.value)
    assert "最多保存 3 个" in msg
    assert "9.9/月" in msg


def test_免费账号一天传第2个被拦(tmp_db, app_secret, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-platform-test-1234567890")
    at, user = _open_documents(tmp_db, "quota_ui_upload_daily")
    quota.record_upload(user["id"])

    with patch("loader.load_and_split_file") as parse:
        _upload_and_parse(at, "second.pdf", _pdf_bytes(1))

    parse.assert_not_called()
    assert any("今日新传文档次数已用完" in w.value for w in at.warning)


def db_date() -> str:
    import quota

    return quota.today_str()
