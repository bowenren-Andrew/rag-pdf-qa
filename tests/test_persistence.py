# -*- coding: utf-8 -*-
"""
文件持久化的单元测试（v3.3 新增）

背景：v3.2 及以前 Chroma 向量只存本地 ./chroma_db，云端重启即丢；
v3.3 起片段与向量全部进数据库（sqlite 本地 / Postgres pgvector 云端），
文档摘要与会话-文档绑定也持久化。

测试原则：
1. 用临时 sqlite（conftest 的 tmp_db），不连接真实 Postgres；
2. 向量模型换成假实现（字符袋向量），**不下载真实模型**，秒级完成；
3. "重启"模拟：v3.3 的 vectorstore 不再持有任何数据缓存（只有模型懒加载），
   因此直接再次调用 db / vectorstore 的读取函数即等价于重启后的全新进程。
"""
import numpy as np
import pytest

import auth
import db
import vectorstore as vs


DIM = 512


class FakeModel:
    """
    假向量模型：把文本映射成「字符袋」向量（每个字符投到固定桶再归一化）。
    两段文本共享的字符越多，余弦相似度越高——足够让排序类断言有意义。
    """

    def encode(self, texts, normalize_embeddings=True):
        if isinstance(texts, str):
            texts = [texts]
        out = []
        for t in texts:
            v = np.zeros(DIM, dtype=np.float32)
            for ch in t:
                v[ord(ch) % DIM] += 1.0
            n = float(np.linalg.norm(v))
            if n > 0:
                v = v / n
            out.append(v)
        return np.array(out)


@pytest.fixture
def fake_model():
    """把 vectorstore 的模型换成假实现（不触发真实模型下载）。"""
    old = vs._model
    vs._model = FakeModel()
    yield vs
    vs._model = old


@pytest.fixture
def two_users(tmp_db):
    """两个测试用户（走真实的注册逻辑，拿到各自 user_id）。"""
    a = auth.register("alice", "password123")["user"]
    b = auth.register("bob", "password123")["user"]
    return a["id"], b["id"]


def _chunks(*texts, page=1):
    """构造 loader 返回格式的片段列表。"""
    return [{"text": t, "page": page} for t in texts]


# ---------------- 基础持久化 ----------------

def test_入库后文档与片段持久化(fake_model, two_users):
    """入库后文档记录与片段都落在数据库里，重启（重新读取）仍可见。"""
    uid, _ = two_users
    n = vs.add_document(uid, "a.pdf", _chunks("第一段内容：苹果是一种水果。", "第二段内容：香蕉也是水果。"))
    assert n == 2
    assert vs.document_exists(uid, "a.pdf")
    assert vs.list_documents(uid) == ["a.pdf"]
    d = db.get_document_by_name(uid, "a.pdf")
    assert d["chunk_count"] == 2
    assert d["doc_type"] == "pdf"
    # 片段真的在数据库里（绕过 vectorstore 直接查）
    ids = db.get_document_ids_by_names(uid, ["a.pdf"])
    rows = db.fetch_chunks_for_similarity(uid, ids)
    assert len(rows) == 2
    assert all(r["text"].startswith(("第一段", "第二段")) for r in rows)


def test_原始文件字节也会持久化(fake_model, two_users):
    """上传时传入的原始文件字节随文档存库，重启后可再取回。"""
    uid, _ = two_users
    vs.add_document(uid, "b.pdf", _chunks("内容足够长。"), file_data=b"%PDF-1.4 fake", file_size=17)
    d = db.get_document_by_name(uid, "b.pdf")
    assert d["has_file"] is True
    assert db.get_document_file(uid, d["id"]) == b"%PDF-1.4 fake"


def test_同名文档重复入库幂等跳过(fake_model, two_users):
    """查重：同名文档第二次 add_document 返回 0，不会产生重复片段。"""
    uid, _ = two_users
    assert vs.add_document(uid, "c.pdf", _chunks("内容一。")) == 1
    assert vs.add_document(uid, "c.pdf", _chunks("内容二。")) == 0
    ids = db.get_document_ids_by_names(uid, ["c.pdf"])
    assert len(db.fetch_chunks_for_similarity(uid, ids)) == 1


def test_摘要持久化并随文档走(fake_model, two_users):
    """摘要写库后，重新读取（等价于重启后加载）仍能拿到。"""
    uid, _ = two_users
    vs.add_document(uid, "d.pdf", _chunks("摘要测试用的正文内容。"))
    brief = {"summary": "这是一份测试文档。", "questions": ["问题一？", "问题二？", "问题三？"]}
    assert vs.save_document_summary(uid, "d.pdf", brief)
    # 「重启」后从数据库读
    got = vs.get_document_summary(uid, "d.pdf")
    assert got == brief
    assert got["questions"][0] == "问题一？"


def test_删除文档清掉片段摘要与绑定(fake_model, two_users):
    """删除文档是彻底的：片段、摘要、会话绑定一起清。"""
    uid, _ = two_users
    vs.add_document(uid, "e.pdf", _chunks("将被删除的内容。"))
    vs.save_document_summary(uid, "e.pdf", {"summary": "s", "questions": []})
    sid = db.create_session(uid, "会话")
    doc_id = db.get_document_by_name(uid, "e.pdf")["id"]
    db.bind_documents_to_session(uid, sid, [doc_id])

    assert vs.delete_document(uid, "e.pdf") == 1
    assert not vs.document_exists(uid, "e.pdf")
    assert vs.get_document_summary(uid, "e.pdf") is None
    assert db.get_session_document_ids(uid, sid) == []
    assert db.get_document(uid, doc_id) is None


# ---------------- 会话-文档绑定 ----------------

def test_绑定后加载会话能恢复文档列表(fake_model, two_users):
    """bind → list_session_documents 返回文档（含摘要），加载旧会话即可恢复。"""
    uid, _ = two_users
    vs.add_document(uid, "f.pdf", _chunks("绑定测试正文。"))
    brief = {"summary": "绑定文档的摘要。", "questions": ["q1？", "q2？", "q3？"]}
    vs.save_document_summary(uid, "f.pdf", brief)
    sid = db.create_session(uid, "有文档的会话")

    assert db.bind_documents_to_session(uid, sid, [db.get_document_by_name(uid, "f.pdf")["id"]])
    docs = db.list_session_documents(uid, sid)
    assert [d["filename"] for d in docs] == ["f.pdf"]
    assert docs[0]["summary"] == brief  # 摘要随文档一起恢复


def test_上传时指定会话会自动绑定(fake_model, two_users):
    """add_document 传 session_id 时自动追加绑定。"""
    uid, _ = two_users
    sid = db.create_session(uid, "上传即绑定")
    vs.add_document(uid, "g.pdf", _chunks("自动绑定的文档。"), session_id=sid)
    ids = db.get_session_document_ids(uid, sid)
    assert len(ids) == 1


def test_未绑定文档的会话检索回退全部(fake_model, two_users):
    """会话没绑定任何文档时，检索范围回退到该用户的全部文档（不答不了问题）。"""
    uid, _ = two_users
    vs.add_document(uid, "h1.pdf", _chunks("苹果是很常见的水果。"))
    vs.add_document(uid, "h2.pdf", _chunks("香蕉产于热带地区。"))
    sid = db.create_session(uid, "空绑定会话")
    hits = vs.similarity_search(uid, "苹果", top_k=4, session_id=sid)
    assert hits, "空绑定会话应回退到全部文档"


def test_删除会话清理绑定但文档保留(fake_model, two_users):
    """删会话只删绑定关系，用户的文档库不受影响。"""
    uid, _ = two_users
    vs.add_document(uid, "i.pdf", _chunks("保留检查。"))
    sid = db.create_session(uid, "将被删除的会话")
    db.bind_documents_to_session(uid, sid, [db.get_document_by_name(uid, "i.pdf")["id"]])
    assert db.delete_session(uid, sid)
    assert db.get_session_document_ids(uid, sid) == []
    assert vs.document_exists(uid, "i.pdf"), "文档本身必须保留"


# ---------------- 用户隔离与越权 ----------------

def test_跨用户文档互不可见(fake_model, two_users):
    """用户 B 查不到 A 的文档（列表、按名查询、取文件都拿不到）。"""
    ua, ub = two_users
    vs.add_document(ua, "secret.pdf", _chunks("A 的私密内容。"), file_data=b"AAA")
    assert vs.list_documents(ub) == []
    assert db.get_document_by_name(ub, "secret.pdf") is None
    assert db.get_document_file(ub, db.get_document_by_name(ua, "secret.pdf")["id"]) is None


def test_检索永不跨用户(fake_model, two_users):
    """B 的检索结果里绝不能出现 A 的片段。"""
    ua, ub = two_users
    vs.add_document(ua, "a_only.pdf", _chunks("A 的苹果园里的苹果很甜。"))
    vs.add_document(ub, "b_only.pdf", _chunks("B 的香蕉园里的香蕉很甜。"))
    hits = vs.similarity_search(ub, "苹果", top_k=4)
    assert hits, "B 应检索到自己的文档"
    assert all(h["source"] == "b_only.pdf" for h in hits), "不能检索到 A 的片段"


def test_跨用户绑定别人的会话或文档失败(fake_model, two_users):
    """B 不能把自己的文档绑到 A 的会话上，也不能把 A 的文档绑到自己会话。"""
    ua, ub = two_users
    vs.add_document(ua, "a.pdf", _chunks("A 的文档。"))
    vs.add_document(ub, "b.pdf", _chunks("B 的文档。"))
    a_sid = db.create_session(ua, "A 的会话")
    b_sid = db.create_session(ub, "B 的会话")
    a_doc = db.get_document_by_name(ua, "a.pdf")["id"]
    b_doc = db.get_document_by_name(ub, "b.pdf")["id"]
    # B 的文档绑到 A 的会话：会话不属于 B → False
    assert not db.bind_documents_to_session(ub, a_sid, [b_doc])
    # A 的文档绑到 B 的会话：文档不属于 B → 不会写入
    assert not db.bind_documents_to_session(ub, b_sid, [a_doc])
    assert db.get_session_document_ids(ub, b_sid) == []


def test_跨用户读取别人的会话文档为空(fake_model, two_users):
    """B 查询 A 会话的绑定 → 空（防越权枚举）。"""
    ua, ub = two_users
    vs.add_document(ua, "ja.pdf", _chunks("A 的文档。"))
    sid = db.create_session(ua, "A 会话")
    db.bind_documents_to_session(ua, sid, [db.get_document_by_name(ua, "ja.pdf")["id"]])
    assert db.get_session_document_ids(ub, sid) == []
    assert db.list_session_documents(ub, sid) == []


def test_跨用户空列表清不掉别人的绑定(fake_model, two_users):
    """B 用空列表 + replace=True 也清不掉 A 会话的绑定（空分支同样校验归属）。"""
    ua, ub = two_users
    vs.add_document(ua, "ka.pdf", _chunks("A 的文档。"))
    a_sid = db.create_session(ua, "A 的会话")
    a_doc = db.get_document_by_name(ua, "ka.pdf")["id"]
    db.bind_documents_to_session(ua, a_sid, [a_doc])
    # B 拿 A 的会话 id 传空列表 + replace=True：应返回 False，绑定原样保留
    assert db.bind_documents_to_session(ub, a_sid, [], replace=True) is False
    assert db.get_session_document_ids(ua, a_sid) == [a_doc], "A 的绑定不能被清掉"


def test_清空自己会话的绑定(fake_model, two_users):
    """A 清空自己会话的绑定：返回 True，绑定被清空（原语义保持）。"""
    ua, _ = two_users
    vs.add_document(ua, "la.pdf", _chunks("A 的文档。"))
    sid = db.create_session(ua, "A 的会话")
    doc_id = db.get_document_by_name(ua, "la.pdf")["id"]
    db.bind_documents_to_session(ua, sid, [doc_id])
    assert db.bind_documents_to_session(ua, sid, [], replace=True) is True
    assert db.get_session_document_ids(ua, sid) == []
    # 文档本身不受影响（只是解除绑定）
    assert vs.document_exists(ua, "la.pdf")


# ---------------- 检索范围与质量 ----------------

def test_检索限定在指定文档列表内(fake_model, two_users):
    """sources 参数限定检索范围，只命中列出的文档。"""
    uid, _ = two_users
    vs.add_document(uid, "k1.pdf", _chunks("苹果是蔷薇科水果。"))
    vs.add_document(uid, "k2.pdf", _chunks("香蕉是芭蕉科水果。"))
    hits = vs.similarity_search(uid, "苹果", top_k=4, sources=["k1.pdf"])
    assert hits
    assert all(h["source"] == "k1.pdf" for h in hits)


def test_检索限定在会话绑定的文档内(fake_model, two_users):
    """session_id 参数：只查该会话绑定的文档，未绑定的文档不参与。"""
    uid, _ = two_users
    vs.add_document(uid, "m1.pdf", _chunks("苹果的甜味来自果糖。"))
    vs.add_document(uid, "m2.pdf", _chunks("香蕉富含钾元素。"))
    sid = db.create_session(uid, "只查苹果的会话")
    db.bind_documents_to_session(uid, sid, [db.get_document_by_name(uid, "m1.pdf")["id"]])
    hits = vs.similarity_search(uid, "苹果", top_k=4, session_id=sid)
    assert hits
    assert all(h["source"] == "m1.pdf" for h in hits), "会话没绑定的文档不应被检索"


def test_相关片段排在前面(fake_model, two_users):
    """余弦排序正确性：与 query 更相关的片段应排第一。"""
    uid, _ = two_users
    vs.add_document(
        uid,
        "n.pdf",
        _chunks("苹果苹果苹果，这里讲的全是苹果。", "香蕉香蕉香蕉，这里讲的全是香蕉。"),
    )
    hits = vs.similarity_search(uid, "苹果是什么", top_k=2)
    assert len(hits) == 2
    assert "苹果" in hits[0]["text"]
    assert hits[0]["score"] <= hits[1]["score"], "score 是距离，越小越相似"


def test_检索结果字段完整(fake_model, two_users):
    """结果必须带 text/source/page/doc_type/score 五个字段（界面依赖）。"""
    uid, _ = two_users
    vs.add_document(uid, "p.pptx", _chunks("幻灯片上的文本内容。", page=3))
    hits = vs.similarity_search(uid, "文本", top_k=1)
    assert hits
    h = hits[0]
    assert set(h) >= {"text", "source", "page", "doc_type", "score"}
    assert h["source"] == "p.pptx"
    assert h["page"] == 3
    assert h["doc_type"] == "pptx"


def test_sqlite下走numpy回退路径(fake_model, tmp_db):
    """本地 sqlite 无 pgvector，检索应走 Python 余弦回退路径且结果正确。"""
    assert db.pgvector_enabled() is False
    import auth as _auth
    u = _auth.register("solo", "password123")["user"]
    vs.add_document(u["id"], "q.pdf", _chunks("回退路径的正文内容。"))
    hits = vs.similarity_search(u["id"], "正文", top_k=1)
    assert hits and hits[0]["source"] == "q.pdf"
