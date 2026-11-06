# -*- coding: utf-8 -*-
"""
DEF-02：已入库旧片段的一次性文本修复。

约束：
1. 只修当前登录用户自己的片段，别的账号一条都不能动；
2. 只改文本（断字 / OCR 字符混淆），不重新抽取、不重新 OCR；
3. 幂等：重复执行时第二次 repaired 必须为 0。
"""
import numpy as np

import db
from loader import repair_chunk_text


def _add_document_with_chunks(user_id: int, filename: str, texts: list[str]) -> int:
    """建一篇文档并写入若干片段（向量随便给个全零数组即可）。"""
    doc_id = db.create_document(user_id, filename)
    rows = [
        {
            "page": 1,
            "text": text,
            "embedding": np.zeros(db.EMBEDDING_DIM, dtype=np.float32).tobytes(),
            "vector": None,
        }
        for text in texts
    ]
    db.insert_chunks(user_id, doc_id, rows)
    return doc_id


def test_repair_chunk_text_修复断字与OCR混淆():
    """后处理同时覆盖邮箱断字、5A TM 与 OCR 字符混淆。"""
    raw = "客服邮箱：suppor t@xinglan-demo.example。防水 5A TM，型号×1，-10C。"
    out = repair_chunk_text(raw)
    assert "support@xinglan-demo.example" in out
    assert "5ATM" in out
    assert "X1" in out
    assert "-10℃" in out


def test_repair_chunk_texts_只修当前用户的片段(tmp_db):
    """A 账号修复后，A 的片段变正确；B 账号同样的旧文本保持原样。"""
    user_a = db.create_user("repair_a", "hash")["id"]
    user_b = db.create_user("repair_b", "hash")["id"]
    doc_a = _add_document_with_chunks(
        user_a, "a.pdf", ["客服邮箱：suppor t@xinglan-demo.example。", "这一条本来就没问题。"]
    )
    doc_b = _add_document_with_chunks(
        user_b, "b.pdf", ["客服邮箱：suppor t@xinglan-demo.example。"]
    )

    stats = db.repair_chunk_texts(user_a, repair_chunk_text)

    assert stats == {"checked": 2, "repaired": 1}
    chunks_a = db.list_document_chunks_preview(user_a, doc_a, limit=10)
    assert "support@xinglan-demo.example" in chunks_a[0]["text"]
    assert "suppor t@" not in chunks_a[0]["text"]
    assert chunks_a[1]["text"] == "这一条本来就没问题。"

    chunks_b = db.list_document_chunks_preview(user_b, doc_b, limit=10)
    assert "suppor t@" in chunks_b[0]["text"], "不能修改其他账号的片段"


def test_repair_chunk_texts_可重复执行(tmp_db):
    """第二次执行时没有可修内容，repaired 应为 0，检查条数不变。"""
    uid = db.create_user("repair_c", "hash")["id"]
    _add_document_with_chunks(uid, "c.pdf", ["suppor t@example.com", "5A TM防水"])

    first = db.repair_chunk_texts(uid, repair_chunk_text)
    second = db.repair_chunk_texts(uid, repair_chunk_text)

    assert first == {"checked": 2, "repaired": 2}
    assert second == {"checked": 2, "repaired": 0}


def test_repair_chunk_texts_没有片段时返回零(tmp_db):
    """空账号直接返回 0，不报错。"""
    uid = db.create_user("repair_d", "hash")["id"]
    assert db.repair_chunk_texts(uid, repair_chunk_text) == {"checked": 0, "repaired": 0}
