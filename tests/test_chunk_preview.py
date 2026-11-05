# -*- coding: utf-8 -*-
"""文档片段预览（BUG-10）的数据库与界面测试。"""
import os

from streamlit.testing.v1 import AppTest

import auth
import db

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)


def _seed_document(user_id: int, filename: str = "preview.pdf") -> int:
    doc_id = db.create_document(user_id, filename)
    rows = [
        {
            "page": page,
            "text": f"片段 {page}：联系 support@xinglan-demo.example",
            "embedding": b"\x00" * 8,
            "vector": None,
        }
        for page in (1, 2, 3, 4)
    ]
    db.insert_chunks(user_id, doc_id, rows)
    return doc_id


def test_片段预览只返回当前用户的前几段(tmp_db):
    alice = db.create_user("preview_alice", auth.hash_password("Password123"))
    bob = db.create_user("preview_bob", auth.hash_password("Password123"))
    doc_id = _seed_document(alice["id"])

    preview = db.list_document_chunks_preview(alice["id"], doc_id, limit=3)

    assert [c["page"] for c in preview] == [1, 2, 3]
    assert all("support@xinglan-demo.example" in c["text"] for c in preview)
    assert db.list_document_chunks_preview(bob["id"], doc_id, limit=3) == []


def test_文档页展示片段预览(tmp_db):
    user = db.create_user("preview_ui", auth.hash_password("Password123"))
    _seed_document(user["id"])
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["user"] = {"id": user["id"], "username": "preview_ui"}
    at.run()
    page_hash = next(
        h for h, info in at._registered_pages.items() if info["page_name"] == "我的文档"
    )
    at._page_hash = page_hash
    at.run()

    labels = [e.label for e in at.expander]
    assert any("片段预览" in label for label in labels)
    assert any("support@xinglan-demo.example" in t.value for t in at.text)
