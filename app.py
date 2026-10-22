# -*- coding: utf-8 -*-
"""
app.py（v2）
职责：Streamlit 界面（单一职责：只做交互与展示，业务逻辑在 loader/vectorstore/qa）。
- 侧边栏：API Key 状态、会话管理（新建 / 加载 / 删除 / 自动保存）、多 PDF 上传入库
- 主区域：文档摘要与推荐问题、流式聊天窗口、命中原文片段与相似度展示
会话历史保存在本地 JSON 文件（./sessions 目录），不使用数据库。
运行方式：streamlit run app.py
"""
import json
import os
import time
from datetime import datetime

import streamlit as st

from loader import load_and_split_pdf
from qa import QAFriendlyError, generate_doc_brief, get_api_key, stream_answer
from vectorstore import add_document, document_exists, list_documents, similarity_search

# 会话文件存放目录（本地 JSON，不涉及数据库）
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SESSION_DIR = os.path.join(PROJECT_ROOT, "sessions")
os.makedirs(SESSION_DIR, exist_ok=True)

st.set_page_config(page_title="PDF 智能问答 v2", page_icon="📄", layout="wide")


# ---------- 访问密码（可选功能，未配置则自动跳过）----------
def _get_access_password() -> str:
    """
    读取访问密码，优先级：Streamlit Secrets > 系统环境变量 > .env 文件。
    三者都没配置时返回空字符串，表示"不启用密码"，本地开发完全不受影响。
    注意：密码只在这里读取并比对，不做任何落盘存储。
    """
    # 1) Streamlit Cloud 的 Secrets（或本地 .streamlit/secrets.toml）
    try:
        pwd = st.secrets.get("APP_PASSWORD", "")
        if pwd:
            return str(pwd)
    except Exception:
        pass  # 本地没有 secrets.toml 时访问 st.secrets 会抛异常，忽略即可
    # 2) 系统环境变量
    pwd = os.environ.get("APP_PASSWORD", "")
    if pwd:
        return pwd
    # 3) 项目根目录 .env
    try:
        from dotenv import dotenv_values

        values = dotenv_values(os.path.join(PROJECT_ROOT, ".env"))
        return str(values.get("APP_PASSWORD") or "")
    except Exception:
        return ""


def check_password() -> bool:
    """未配置密码直接放行；已配置则弹登录框校验。返回 True 表示允许访问。"""
    expected = _get_access_password()
    if not expected:
        return True  # 没设密码 = 不启用，本地照常使用
    if st.session_state.get("auth_ok"):
        return True  # 本次会话已验证过，后续刷新不再重复输入

    # 用一个居中的卡片做登录界面
    left, mid, right = st.columns([1, 2, 1])
    with mid:
        st.markdown("### 🔒 需要访问密码")
        st.caption("请输入访问密码后使用本系统")
        with st.form("login_form"):
            pwd = st.text_input("访问密码", type="password", placeholder="请输入密码")
            submitted = st.form_submit_button("进入", use_container_width=True)
        if submitted:
            if pwd == expected:
                st.session_state["auth_ok"] = True
                st.rerun()  # 重新运行，让页面正常渲染
            else:
                st.error("密码不正确，请重试")
    return False


# 校验不通过就停止渲染后面的所有内容（这是 Streamlit 官方推荐的拦截方式）
if not check_password():
    st.stop()

st.title("📄 基于 RAG 的 PDF 问答 · v2")
st.caption("多 PDF 入库 → 自动生成摘要与推荐问题 → 流式问答，答案附带命中原文与相似度")


# ---------- 会话管理：本地 JSON 读写 ----------
def _now_str() -> str:
    """当前时间字符串，用于会话的创建/更新时间。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _session_path(sid: str) -> str:
    """会话 ID 对应的 JSON 文件路径。"""
    return os.path.join(SESSION_DIR, f"{sid}.json")


def list_sessions() -> list[dict]:
    """读取所有历史会话，按更新时间倒序返回。"""
    items = []
    if not os.path.isdir(SESSION_DIR):
        return items
    for name in os.listdir(SESSION_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSION_DIR, name), "r", encoding="utf-8") as f:
                items.append(json.load(f))
        except Exception:
            continue  # 跳过损坏的文件，不影响整体加载
    items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return items


def save_current_session():
    """把当前会话写入本地 JSON（自动保存）。"""
    data = {
        "id": st.session_state.session_id,
        "title": st.session_state.session_title,
        "created_at": st.session_state.session_created,
        "updated_at": _now_str(),
        "messages": st.session_state.messages,
    }
    try:
        with open(_session_path(data["id"]), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"会话保存失败：{e}")


def delete_session(sid: str):
    """删除指定会话文件。"""
    path = _session_path(sid)
    if os.path.exists(path):
        os.remove(path)


def new_session():
    """新建一个空会话（不立即写文件，首次提问后自动保存）。"""
    st.session_state.session_id = f"{int(time.time())}_{os.urandom(3).hex()}"
    st.session_state.session_title = "新会话"
    st.session_state.session_created = _now_str()
    st.session_state.messages = []


# ---------- 会话状态初始化 ----------
if "session_id" not in st.session_state:
    new_session()
if "doc_briefs" not in st.session_state:
    st.session_state.doc_briefs = {}  # {文件名: {"summary":..., "questions":[...]}}
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None  # 点击推荐问题后待发送的问题


def render_sources(sources: list[dict]):
    """折叠展示命中的原文片段及相似度分数。"""
    with st.expander("📚 命中原文片段与相似度"):
        for i, s in enumerate(sources, start=1):
            # Chroma 返回的是余弦距离（越小越相似），换算成百分比相似度展示
            sim = max(0.0, 1.0 - float(s["score"])) * 100
            st.markdown(
                f"**【片段{i}】** {s['source']} · 第 {s['page']} 页 · 相似度 **{sim:.1f}%**"
            )
            st.caption(s["text"])
            st.progress(min(max(sim / 100.0, 0.0), 1.0))


def handle_question(question: str):
    """处理一次提问：检索 → 流式生成 → 展示来源 → 自动保存会话。"""
    # 首次提问时用问题内容给会话命名，方便历史列表辨认
    if not st.session_state.messages:
        st.session_state.session_title = question[:20]

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    contexts, answer = [], None
    with st.chat_message("assistant"):
        try:
            contexts = similarity_search(question, top_k=4)
            # write_stream 会逐段消费生成器，实现打字机效果
            answer = st.write_stream(stream_answer(question, contexts))
        except QAFriendlyError as e:
            # 业务级错误（Key 缺失、API 失败等）：友好中文提示，不崩溃
            st.warning(str(e))
        except Exception as e:
            st.error(f"出现未预期的错误：{e}")

        if answer is not None:
            render_sources(contexts)
            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "sources": contexts}
            )
        else:
            st.session_state.messages.append(
                {"role": "assistant", "content": "⚠️ 本次未生成回答，请检查 API Key 或网络后重试。"}
            )
    save_current_session()


# ---------- 侧边栏 ----------
with st.sidebar:
    st.header("🔑 API Key")
    if get_api_key():
        st.success("已从 .env 读取 DEEPSEEK_API_KEY")
    else:
        st.error("未检测到 DEEPSEEK_API_KEY，请在项目根目录 .env 中配置")

    st.header("💬 会话管理")
    if st.button("➕ 新建会话", use_container_width=True):
        new_session()
        st.rerun()

    sessions = list_sessions()
    if not sessions:
        st.caption("暂无历史会话")
    for s in sessions:
        cols = st.columns([0.62, 0.19, 0.19])
        flag = "✅ " if s.get("id") == st.session_state.session_id else ""
        cols[0].write(f"{flag}{s.get('title', '未命名会话')}")
        if cols[1].button("加载", key=f"load_{s.get('id')}"):
            st.session_state.session_id = s.get("id")
            st.session_state.session_title = s.get("title", "未命名会话")
            st.session_state.session_created = s.get("created_at", _now_str())
            st.session_state.messages = s.get("messages", [])
            st.rerun()
        if cols[2].button("删除", key=f"del_{s.get('id')}"):
            delete_session(s.get("id"))
            if s.get("id") == st.session_state.session_id:
                new_session()
            st.rerun()

    st.divider()
    st.header("📁 文档管理")
    # 支持同时上传多个 PDF
    uploaded_files = st.file_uploader(
        "上传 PDF（可多选）", type=["pdf"], accept_multiple_files=True
    )
    if uploaded_files and st.button("解析并入库", type="primary", use_container_width=True):
        for f in uploaded_files:
            with st.spinner(f"正在处理《{f.name}》…"):
                if document_exists(f.name):
                    st.info(f"《{f.name}》已入库，跳过。")
                    continue
                try:
                    chunks = load_and_split_pdf(f)
                    if not chunks:
                        st.warning(f"《{f.name}》未提取到文本（可能是扫描件）。")
                        continue
                    n = add_document(f.name, chunks)
                    st.success(f"《{f.name}》入库完成：{n} 个片段")
                    # 入库后自动生成全文摘要 + 3 个推荐问题（只在该文档内检索）
                    ctx = similarity_search(
                        "本文的主要内容、核心观点、结论与关键信息是什么",
                        top_k=6,
                        source=f.name,
                    )
                    st.session_state.doc_briefs[f.name] = generate_doc_brief(f.name, ctx)
                except QAFriendlyError as e:
                    st.warning(f"《{f.name}》：{e}")
                except Exception as e:
                    st.error(f"《{f.name}》处理失败：{e}")

    docs = list_documents()
    if docs:
        st.caption("知识库：" + "、".join(docs))

# ---------- 主区域：文档摘要与推荐问题 ----------
if st.session_state.doc_briefs:
    for name, brief in st.session_state.doc_briefs.items():
        with st.expander(f"📝 《{name}》全文摘要与推荐问题", expanded=False):
            st.markdown(brief.get("summary", ""))
            questions = brief.get("questions") or []
            if questions:
                st.markdown("**推荐问题（点击直接提问）**")
                for i, q in enumerate(questions):
                    if st.button(q, key=f"sug_{name}_{i}"):
                        st.session_state.pending_question = q

st.divider()
st.subheader(f"💬 当前会话：{st.session_state.session_title}")

# 渲染历史消息（含引用来源）
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            render_sources(msg["sources"])

# 输入框提问
typed = st.chat_input("请输入你的问题…")
if typed:
    handle_question(typed)

# 点击推荐问题提问（在同一轮运行中消费 pending_question）
if st.session_state.pending_question:
    q = st.session_state.pending_question
    st.session_state.pending_question = None
    handle_question(q)
