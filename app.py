# -*- coding: utf-8 -*-
"""
app.py（v3.3）
职责：Streamlit 界面（单一职责：只做交互与展示，业务逻辑在 auth/db/loader/vectorstore/qa）。
- v3 变更：新增账号体系（注册/登录）、session_state 保存当前用户；
          知识库与会话按 user_id 隔离；每位用户使用自己的 DeepSeek Key。
- v3.1 变更：问答支持多轮对话上下文（history 传入 qa 层）；
          文档页支持 PDF 与 PPTX 上传（按后缀分发解析）；
          图片型 PDF 走 OCR 兜底，识别失败的页码用一条提示列出；
          来源展示按类型区分「第 X 页」/「第 X 张」。
- v3.2 变更：文档页新增「强制全文 OCR」开关，解决公式类 PDF 提取不出公式的问题。
- v3.3 变更（持久化）：文档与片段存数据库（Chroma 退役），云端重启后不丢；
          新增会话-文档绑定：加载旧会话恢复其文档列表与摘要；
          问答检索默认限定在本会话选定的文档内。
- 页面结构（st.navigation 多页面）：
  1) 💬 问答      —— 聊天、流式回答、引用原文与相似度、文档摘要与推荐问题、会话管理
  2) 📁 我的文档  —— 上传 PDF / PPTX 入库（查重、存库）、已入库列表与删除
  3) ⚙️ 设置      —— 账号信息、API Key 管理（加密存储 + 掩码显示）、修改密码
会话数据存放在数据库（见 db.py），不再使用本地 JSON 文件。
运行方式：streamlit run app.py
"""
import logging
import os

import streamlit as st

import auth
import db
from loader import FileParseError, load_and_split_file
from qa import QAFriendlyError, generate_doc_brief, stream_answer, verify_api_key
from vectorstore import (
    add_document,
    delete_document,
    document_exists,
    list_document_details,
    save_document_summary,
    similarity_search,
)

logger = logging.getLogger(__name__)

# 原始文件字节入库上限：超过则不存原始文件（片段与摘要仍会持久化），
# 避免撑爆 Supabase 免费版数据库（500MB）
MAX_FILE_BYTES = 20 * 1024 * 1024

# 提问输入上限（BUG-08）：显示「已输入 / 上限」，超过上限时提示并禁止提交
MAX_QUESTION_CHARS = 2000

st.set_page_config(
    page_title="PDF 智能问答",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------- 云端适配：把 Streamlit Secrets 同步进环境变量 ----------
def _sync_secrets_to_env():
    """
    Streamlit Cloud 上配置的 Secrets 不一定自动出现在 os.environ 里，
    而 db.py / auth.py 是读环境变量的。这里显式同步一次，
    保证「本地 .env」和「云端 Secrets」两种部署方式走同一套读取逻辑。
    """
    try:
        for key in ("DATABASE_URL", "APP_SECRET_KEY"):
            if not os.environ.get(key):  # 已存在的环境变量不覆盖
                value = st.secrets.get(key, "")
                if value:
                    os.environ[key] = str(value)
    except Exception:
        pass  # 本地没有 secrets.toml 时访问 st.secrets 会抛异常，忽略即可


_sync_secrets_to_env()

try:
    db.init_db()
except Exception as e:
    st.error(f"数据库连接失败：{e}")
    st.stop()


# ---------- 极少量自定义 CSS：只做圆角、分隔线与卡片阴影，不加动画 ----------
CUSTOM_CSS = """
<style>
  /* 主标题区渐变分隔线 */
  .title-divider {
    height: 3px;
    border-radius: 3px;
    margin: 6px 0 18px 0;
    background: linear-gradient(90deg, #2563EB 0%, #60A5FA 55%, rgba(37,99,235,0) 100%);
  }
  /* 登录/注册卡片：圆角 + 浅阴影 */
  .login-card {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 16px;
    box-shadow: 0 2px 14px rgba(15, 23, 42, 0.06);
    padding: 8px 24px 20px 24px;
  }
  /* 主要按钮圆角 */
  .stButton > button, .stFormSubmitButton > button {
    border-radius: 10px;
  }
  /* 侧边栏用户区：圆形头像 + 用户名 */
  .user-chip { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
  .avatar {
    width: 34px; height: 34px; border-radius: 50%;
    background: #2563EB; color: #FFFFFF;
    display: flex; align-items: center; justify-content: center;
    font-size: 15px; font-weight: 600; flex: none;
  }
  .user-name { font-size: 14px; font-weight: 600; color: #111827; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------- 会话状态初始化 ----------
def _reset_chat_state():
    """清空内存中与当前会话相关的状态（切换用户或新建会话时使用）。"""
    st.session_state.session_id = None  # 数据库会话 ID，首次提问时才真正创建
    st.session_state.session_title = "新会话"
    st.session_state.messages = []
    st.session_state.doc_briefs = {}  # {文件名: {"summary":..., "questions":[...]}}
    st.session_state.pending_question = None  # 点击推荐问题后待发送的问题
    # v3.3：会话-文档绑定（检索范围）与「会话还没创建时先记着」的待绑定文档
    st.session_state.session_doc_ids = []  # 当前会话已绑定的文档 ID
    st.session_state.pending_doc_ids = []  # 会话创建后要立即绑定的文档 ID
    st.session_state.session_scope_names = []  # 当前检索范围（文件名列表，空=全部）


if "user" not in st.session_state:
    st.session_state.user = None  # {"id": int, "username": str}
if "session_id" not in st.session_state:
    _reset_chat_state()


# ---------- 登录 / 注册页 ----------
def render_auth_page():
    """未登录时展示的居中登录/注册卡片。"""
    left, mid, right = st.columns([1, 1.4, 1])
    with mid:
        st.markdown('<div class="title-divider"></div>', unsafe_allow_html=True)
        st.markdown("## 📄 PDF 智能问答")
        st.caption("上传 PDF → 自动摘要与推荐问题 → 流式问答，答案附带原文出处与相似度")
        st.markdown('<div class="login-card">', unsafe_allow_html=True)

        # 用 radio 代替 tabs：注册成功后可以直接切回登录页并回填用户名。
        # 切换发生在下一次 rerun、radio 实例化之前，避免直接改 widget state 被忽略。
        if st.session_state.pop("_switch_to_login", False):
            st.session_state.pop("auth_mode", None)
        mode = st.radio(
            "账号操作",
            ["登录", "注册"],
            horizontal=True,
            label_visibility="collapsed",
            key="auth_mode",
        )
        notice = st.session_state.pop("auth_notice", None)
        if notice:
            st.success(notice)
        if mode == "登录":
            render_login_form()
        else:
            render_register_form()

        st.markdown("</div>", unsafe_allow_html=True)


def render_login_form():
    """登录表单：失败统一提示「用户名或密码不正确」。"""
    with st.form("login_form"):
        username = st.text_input(
            "用户名",
            placeholder="3–20 位字母、数字或下划线",
            key="login_username",
        )
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", use_container_width=True, type="primary")
    if submitted:
        try:
            user = auth.login(username, password)
        except Exception as e:
            st.error(f"登录失败：{e}")
            return
        if user:
            st.session_state.user = {"id": user["id"], "username": user["username"]}
            _reset_chat_state()
            st.rerun()
        else:
            st.error("用户名或密码不正确")


def render_register_form():
    """注册表单：校验用户名与密码规则，重名给出中文提示。"""
    with st.form("register_form"):
        username = st.text_input("设置用户名", placeholder="3–20 位字母、数字或下划线")
        password = st.text_input("设置密码", type="password", help="至少 8 位")
        password2 = st.text_input("确认密码", type="password")
        submitted = st.form_submit_button("注册", use_container_width=True)
    if submitted:
        if password != password2:
            st.error("两次输入的密码不一致。")
            return
        try:
            result = auth.register(username, password)
        except Exception as e:
            st.error(f"注册失败：{e}")
            return
        if result["ok"]:
            # BUG-09：自动切到登录页，并把刚注册的用户名填好
            st.session_state.login_username = username.strip()
            st.session_state.auth_notice = "注册成功，请输入密码登录。"
            st.session_state._switch_to_login = True
            st.rerun()
        else:
            st.error(result["msg"])


def render_sidebar_user():
    """侧边栏顶部：当前用户的圆形头像、用户名与退出登录按钮。"""
    user = st.session_state.user
    with st.sidebar:
        initial = (user["username"][0] if user["username"] else "?").upper()
        st.markdown(
            f'<div class="user-chip"><div class="avatar">{initial}</div>'
            f'<span class="user-name">{user["username"]}</span></div>',
            unsafe_allow_html=True,
        )
        if st.button("退出登录", use_container_width=True):
            st.session_state.user = None
            _reset_chat_state()
            st.rerun()
        st.divider()


# 未登录：只显示登录/注册页（st.stop 阻断后面所有内容）
if st.session_state.user is None:
    render_auth_page()
    st.stop()

render_sidebar_user()

USER_ID = st.session_state.user["id"]


def _drop_doc_from_session_state(doc_id: int, name: str):
    """文档删除后同步清理内存里的摘要与绑定，避免界面继续引用已删文档。"""
    st.session_state.doc_briefs.pop(name, None)
    st.session_state.session_doc_ids = [
        i for i in st.session_state.get("session_doc_ids", []) if i != doc_id
    ]
    st.session_state.pending_doc_ids = [
        i for i in st.session_state.get("pending_doc_ids", []) if i != doc_id
    ]
    st.session_state.session_scope_names = [
        n for n in st.session_state.get("session_scope_names", []) if n != name
    ]


@st.dialog("确认删除文档")
def _confirm_delete_document():
    """删除文档前的二次确认：确认后才删片段、摘要、原始文件与会话绑定。"""
    target = st.session_state.get("pending_delete_doc") or {}
    name = target.get("name", "")
    if not name:
        st.session_state.pending_delete_doc = None
        st.rerun()

    st.write(f"确定要删除《{name}》吗？")
    st.caption(
        "该文档的全部片段、摘要、原始文件与会话绑定将一并从数据库删除，"
        "删除后无法恢复。"
    )
    confirm_col, cancel_col = st.columns(2)
    if confirm_col.button(
        "确认删除", type="primary", use_container_width=True, key="confirm_doc_delete"
    ):
        try:
            removed = delete_document(USER_ID, name)
            _drop_doc_from_session_state(target.get("id"), name)
            st.session_state.pending_delete_doc = None
            st.session_state.doc_flash = (
                f"已删除《{name}》（{removed} 个片段），可重新上传。"
                if removed
                else f"《{name}》已不存在。"
            )
            st.rerun()
        except Exception:
            logger.exception("删除文档《%s》失败", name)
            st.error("删除失败，请稍后重试。")
    if cancel_col.button("取消", use_container_width=True, key="cancel_doc_delete"):
        st.session_state.pending_delete_doc = None
        st.rerun()


@st.dialog("确认删除会话")
def _confirm_delete_session():
    """删除历史会话前的二次确认：会话记录与会话-文档绑定一起删除。"""
    target = st.session_state.get("pending_delete_session") or {}
    session_id = target.get("id")
    title = target.get("title") or "未命名会话"
    if not session_id:
        st.session_state.pending_delete_session = None
        st.rerun()

    st.write(f"确定要删除会话「{title}」吗？")
    st.caption(
        "该会话的全部聊天记录与会话-文档绑定将一并删除，删除后无法恢复"
        "（知识库里的文档本身不会被删除）。"
    )
    confirm_col, cancel_col = st.columns(2)
    if confirm_col.button(
        "确认删除", type="primary", use_container_width=True, key="confirm_session_delete"
    ):
        try:
            db.delete_session(USER_ID, session_id)
            if session_id == st.session_state.session_id:
                _reset_chat_state()
            st.session_state.pending_delete_session = None
            st.rerun()
        except Exception:
            logger.exception("删除会话 %s 失败", session_id)
            st.error("删除失败，请稍后重试。")
    if cancel_col.button("取消", use_container_width=True, key="cancel_session_delete"):
        st.session_state.pending_delete_session = None
        st.rerun()


def _current_api_key() -> str:
    """读取当前用户解密后的 DeepSeek Key；未设置返回空字符串。"""
    try:
        return auth.get_user_api_key(USER_ID)
    except auth.AuthFriendlyError as e:
        st.sidebar.warning(str(e))
        return ""


def _require_key_or_guide() -> str:
    """
    取当前用户的 Key；没填时给出引导提示并返回空字符串（不抛异常、不崩溃）。
    """
    key = _current_api_key()
    if not key:
        st.warning("还没有填写 DeepSeek API Key，请到「⚙️ 设置」页填写后再使用提问功能。")
    return key


# ---------- 通用组件 ----------
def render_sources(sources: list[dict]):
    """折叠展示命中的原文片段及相似度分数（PDF 用「页」，PPTX 用「张」）。"""
    with st.expander("📚 命中原文片段与相似度"):
        for i, s in enumerate(sources, start=1):
            # Chroma 返回的是余弦距离（越小越相似），换算成百分比相似度展示
            sim = max(0.0, 1.0 - float(s["score"])) * 100
            unit = "张" if s.get("doc_type") == "pptx" else "页"
            st.markdown(
                f"**【片段{i}】** {s['source']} · 第 {s['page']} {unit} · 相似度 **{sim:.1f}%**"
            )
            st.caption(s["text"])
            st.progress(min(max(sim / 100.0, 0.0), 1.0))


def save_current_session():
    """把当前会话写回数据库（自动保存，只写自己的会话）。"""
    if not st.session_state.session_id:
        return
    db.save_session(
        USER_ID,
        st.session_state.session_id,
        st.session_state.messages,
        title=st.session_state.session_title,
    )


def _load_session_docs(session_id: int):
    """
    v3.3：按绑定关系恢复某个会话的文档列表与摘要（db 只返回当前用户的数据）。
    - 文档列表写进 session_doc_ids（决定检索范围）；
    - 每篇文档持久化的摘要恢复进 doc_briefs（问答页重新可见、可点击推荐问题）。
    """
    docs = db.list_session_documents(USER_ID, session_id)
    st.session_state.session_doc_ids = [d["id"] for d in docs]
    st.session_state.session_scope_names = [d["filename"] for d in docs]
    st.session_state.doc_briefs = {}
    for d in docs:
        if d.get("summary"):
            st.session_state.doc_briefs[d["filename"]] = d["summary"]


def _bind_pending_docs(session_id: int):
    """v3.3：把上传时（会话尚未创建）记下的文档绑定到刚创建的会话上。"""
    pending = st.session_state.get("pending_doc_ids") or []
    if pending and session_id:
        db.bind_documents_to_session(
            USER_ID, session_id, pending, replace=False
        )
        st.session_state.session_doc_ids = list(
            set(st.session_state.get("session_doc_ids", []) + pending)
        )
        st.session_state.pending_doc_ids = []


def _doc_summary_of(filename: str) -> dict | None:
    """从数据库读取某篇文档持久化的摘要（没生成过或已删除则返回 None）。"""
    try:
        d = db.get_document_by_name(USER_ID, filename)
        return d.get("summary") if d else None
    except Exception:
        return None


def _file_bytes_for_storage(f) -> bytes | None:
    """
    读取上传文件的原始字节用于持久化；超过上限（20MB）返回 None。
    片段与摘要仍会正常入库，只是不再保存原始文件。
    """
    try:
        data = f.getvalue()
    except Exception:
        return None
    if data and len(data) > MAX_FILE_BYTES:
        st.caption("（原始文件超过 20MB，仅保存解析后的片段与摘要）")
        return None
    return data


# ---------- 页面一：💬 问答 ----------
def page_chat():
    """问答页：聊天窗口、流式回答、引用来源、摘要推荐问题、会话管理。"""
    st.markdown('<div class="title-divider"></div>', unsafe_allow_html=True)

    # 上一轮成功提交后清空输入框：必须在 text_area 实例化之前修改 key
    if st.session_state.pop("_clear_question_input", False):
        st.session_state["question_input"] = ""

    # 会话管理（数据来自数据库，只查当前用户的）
    with st.sidebar:
        st.subheader("💬 会话管理")
        if st.button("➕ 新建会话", use_container_width=True):
            _reset_chat_state()
            st.rerun()

        sessions = db.list_sessions(USER_ID)
        if not sessions:
            st.caption("暂无历史会话")
        for s in sessions:
            cols = st.columns([0.62, 0.19, 0.19])
            flag = "✅ " if s["id"] == st.session_state.session_id else ""
            cols[0].write(f"{flag}{s.get('title', '未命名会话')}")
            if cols[1].button("加载", key=f"load_{s['id']}"):
                loaded = db.get_session(USER_ID, s["id"])  # 再次校验归属，防越权
                if loaded:
                    st.session_state.session_id = loaded["id"]
                    st.session_state.session_title = loaded["title"]
                    st.session_state.messages = loaded["messages"]
                    _load_session_docs(loaded["id"])  # 恢复该会话的文档与摘要
                    st.rerun()
            if cols[2].button("删除", key=f"del_{s['id']}"):
                # 只记录待删目标，弹窗确认后才真正删除
                st.session_state.pending_delete_session = {
                    "id": s["id"],
                    "title": s.get("title") or "未命名会话",
                }

    if st.session_state.get("pending_delete_session"):
        _confirm_delete_session()

    # v3.3：本会话检索范围——多选自己的文档，检索只查选中的；
    # 加载旧会话时默认选中该会话绑定的文档；没绑定过则默认全部
    all_docs = list_document_details(USER_ID)
    all_names = [d["filename"] for d in all_docs]
    scope_names = []
    if all_names:
        with st.sidebar:
            st.divider()
            st.subheader("📎 本会话文档")
            bound_names = [
                d["filename"]
                for d in db.list_session_documents(
                    USER_ID, st.session_state.session_id
                )
            ] if st.session_state.session_id else []
            default_names = bound_names or all_names
            # 选择即时生效：多选框不放在 form 里，改动即触发重跑并同步检索范围；
            # 有会话时直接把绑定写进数据库（replace=True 覆盖旧绑定），
            # 会话还没创建时先记为待绑定（首次提问创建会话后立即保存）。
            selected = st.multiselect(
                "问答只检索选中的文档",
                options=all_names,
                default=default_names,
                help=(
                    "勾选改动即时生效并自动保存到当前会话；"
                    "加载旧会话时自动恢复当时的选择。"
                ),
            )
            scope_names = selected
            selected_list = list(selected)
            prev_scope = list(st.session_state.get("session_scope_names") or [])
            # 初始未触碰状态（无会话、无历史范围、选择等于默认值）不算改动，
            # 避免刚进页面就把 pending 填满，保持「未选=覆盖全部」的原有语义。
            untouched_first_run = (
                not st.session_state.session_id
                and not prev_scope
                and selected_list == list(default_names)
            )
            if not untouched_first_run and selected_list != prev_scope:
                ids = db.get_document_ids_by_names(USER_ID, selected_list)
                if st.session_state.session_id:
                    db.bind_documents_to_session(
                        USER_ID, st.session_state.session_id, ids, replace=True
                    )
                    st.session_state.session_doc_ids = ids
                else:
                    # 会话要等首次提问才创建：先记为待绑定，创建后立即保存
                    st.session_state.pending_doc_ids = ids
                    st.caption("已记下选择，首次提问后会随会话一起保存。")
                st.session_state.session_scope_names = selected_list
            if not scope_names:
                st.caption("（未选择任何文档：检索将覆盖你的全部文档）")
    st.session_state.session_scope_names = scope_names

    # 摘要与推荐问题跟随检索范围展示（持久化在文档上的摘要即时可见）
    for name in scope_names:
        brief = st.session_state.doc_briefs.get(name)
        if brief is None:
            brief = _doc_summary_of(name)  # 从数据库读（新增文档或旧会话恢复）
        if brief:
            st.session_state.doc_briefs[name] = brief

    # 主区：上次入库生成的摘要与推荐问题
    for name, brief in st.session_state.doc_briefs.items():
        with st.expander(f"📝 《{name}》全文摘要与推荐问题", expanded=False):
            st.markdown(brief.get("summary", ""))
            questions = brief.get("questions") or []
            if questions:
                st.markdown("**推荐问题（点击直接提问）**")
                for i, q in enumerate(questions):
                    if st.button(q, key=f"sug_{name}_{i}"):
                        st.session_state.pending_question = q

    st.subheader(f"💬 当前会话：{st.session_state.session_title}")

    # 渲染历史消息（含引用来源）
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sources"):
                render_sources(msg["sources"])

    # BUG-08：输入框下方实时显示「已输入 / 上限」，超过上限时提示并禁用发送。
    input_col, button_col = st.columns([0.85, 0.15])
    typed = input_col.text_area(
        "请输入你的问题…",
        placeholder=f"最多 {MAX_QUESTION_CHARS} 字",
        height=90,
        key="question_input",
        label_visibility="collapsed",
    )
    typed = typed or ""
    question_len = len(typed)
    too_long = question_len > MAX_QUESTION_CHARS
    if too_long:
        st.warning(
            f"问题已输入 {question_len} 字，超过上限 {MAX_QUESTION_CHARS} 字，"
            "请删减后再发送。"
        )
    st.caption(f"已输入 {question_len} / {MAX_QUESTION_CHARS} 字")
    submitted = button_col.button(
        "发送",
        type="primary",
        use_container_width=True,
        disabled=not typed.strip() or too_long,
    )
    if submitted:
        typed = (typed or "").strip()
        if not typed:
            st.warning("请输入问题后再发送。")
        elif len(typed) > MAX_QUESTION_CHARS:
            st.warning(f"问题不能超过 {MAX_QUESTION_CHARS} 字，请缩短后重试。")
        else:
            if handle_question(typed):
                st.session_state._clear_question_input = True
                st.rerun()

    # 点击推荐问题提问（在同一轮运行中消费 pending_question）
    if st.session_state.pending_question:
        q = st.session_state.pending_question
        st.session_state.pending_question = None
        handle_question(q)


def _build_history() -> list[dict]:
    """
    整理多轮对话上下文：只保留 role 与 content 两个字段，去掉 sources 等额外字段。
    注意：必须在把本轮新问题追加进 messages **之前**调用，避免问题被重复计入历史。
    """
    return [
        {"role": m.get("role", "user"), "content": m.get("content", "")}
        for m in st.session_state.get("messages", [])
        if m.get("role") in ("user", "assistant")
    ]


def handle_question(question: str) -> bool:
    """处理一次提问：取用户 Key → 检索 → 带历史流式生成 → 展示来源 → 自动保存会话。"""
    api_key = _require_key_or_guide()
    if not api_key:
        return False

    # 首次提问时才在数据库创建会话记录，并以前 20 字作为会话标题
    if not st.session_state.session_id:
        st.session_state.session_title = question[:20]
        st.session_state.session_id = db.create_session(USER_ID, st.session_state.session_title)
        _bind_pending_docs(st.session_state.session_id)  # 把记下的文档选择一并绑定

    # 先取历史（此时 messages 里还没有本轮问题），再把本轮问题追加进去
    history = _build_history()
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # v3.3：检索默认限定在本会话选定的文档内；没选则覆盖全部文档
    scope = st.session_state.get("session_scope_names") or []
    contexts, answer = [], None
    with st.chat_message("assistant"):
        try:
            contexts = similarity_search(
                USER_ID, question, top_k=4, sources=scope or None
            )
            # write_stream 会逐段消费生成器，实现打字机效果
            answer = st.write_stream(
                stream_answer(question, contexts, history=history, api_key=api_key)
            )
        except QAFriendlyError as e:
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
    return True


# ---------- 页面二：📁 我的文档 ----------
def page_documents():
    """文档页：多文件上传入库（PDF / PPTX，查重）、已入库列表、逐文档删除。"""
    st.markdown('<div class="title-divider"></div>', unsafe_allow_html=True)
    st.subheader("📁 我的文档")
    st.caption(
        "支持 PDF（含扫描件，图片页会自动 OCR 识别）与 PPTX；"
        "上传的文档只会进入你自己的知识库，其他用户看不到。"
    )
    flash = st.session_state.pop("doc_flash", None)
    if flash:
        st.success(flash)

    uploaded_files = st.file_uploader(
        "上传 PDF 或 PPTX（可多选）",
        accept_multiple_files=True,
        help="仅支持 PDF 与 PPTX；其他格式会在提交时提示，不会入库。",
    )
    # 不在 file_uploader 里限制 type：Streamlit 自带的英文报错无法改成中文，
    # 改为选中后由我们校验后缀并给出中文提示。
    unsupported_uploads = [
        f
        for f in (uploaded_files or [])
        if os.path.splitext(f.name)[1].lower() not in (".pdf", ".pptx")
    ]
    if unsupported_uploads:
        names = "、".join(f"《{f.name}》" for f in unsupported_uploads)
        st.warning(f"{names}格式不支持，仅支持 PDF 与 PPTX，请移除后重新上传。")

    # 强制全文 OCR 开关：默认关闭，勾选后对 PDF 的每一页都做 OCR（对 PPTX 无效）
    force_ocr = False
    if uploaded_files:
        force_ocr = st.checkbox(
            "该文件含大量数学公式或扫描质量差，强制全文 OCR（较慢、更占内存，大文件慎用）",
            value=False,
            help=(
                "适用于 LaTeX 导出的课件/作业：这类 PDF 能提取到文字，但公式位置是空白，"
                "常规规则（整页少于 10 字才 OCR）不会触发。勾选后忽略文本层、逐页识别，"
                "普通 PDF 请不要勾选。仅对 PDF 生效。"
            ),
        )

    if uploaded_files and st.button("解析并入库", type="primary"):
        api_key = _current_api_key()  # 摘要生成需要 Key，没有则跳过摘要
        for f in uploaded_files:
            if os.path.splitext(f.name)[1].lower() not in (".pdf", ".pptx"):
                st.warning(f"《{f.name}》格式不支持，仅支持 PDF 与 PPTX，已跳过。")
                continue
            with st.spinner(f"正在处理《{f.name}》…"):
                if document_exists(USER_ID, f.name):
                    st.info(f"《{f.name}》已入库，跳过。如需重新处理，请先删除它。")
                    continue
                try:
                    # 按后缀分发解析；OCR 情况写进 report，供下方提示使用
                    ocr_report = {}
                    chunks = load_and_split_file(
                        f, f.name, ocr_report=ocr_report, force_ocr=force_ocr
                    )
                except FileParseError as e:
                    st.error(f"《{f.name}》解析失败：{e}")
                    continue
                except Exception:
                    # 底层异常原文只进日志，界面只给可操作的中文提示
                    logger.exception("解析《%s》失败", f.name)
                    st.error(f"《{f.name}》解析失败：文件无法读取，请确认文件完好后重试。")
                    continue

                if not chunks:
                    # 只有全文完全没有可用文本时才这样提示（单个图片页失败另有提示）
                    st.warning(f"《{f.name}》未提取到任何可用文本，已跳过。")
                    continue

                # OCR 结果提示：识别成功的页与识别失败被跳过的页
                if ocr_report.get("ocr_pages"):
                    if ocr_report.get("ocr_mode") == "force":
                        st.info(
                            f"《{f.name}》已强制全文 OCR，"
                            f"共 {len(ocr_report['ocr_pages'])} 页通过识别入库。"
                        )
                    else:
                        st.info(
                            f"《{f.name}》有 {len(ocr_report['ocr_pages'])} 个图片页已通过 OCR 识别入库。"
                        )
                if ocr_report.get("ocr_failed"):
                    pages = "、".join(str(p) for p in ocr_report["ocr_failed"])
                    st.warning(f"第 {pages} 页为图片且识别失败，已跳过。")
                if ocr_report.get("ocr_error") and not ocr_report.get("ocr_pages"):
                    logger.warning("《%s》OCR 不可用：%s", f.name, ocr_report["ocr_error"])
                    st.warning("OCR 未启用（图片识别组件不可用），图片页内容可能被跳过。")

                try:
                    n = add_document(
                        USER_ID,
                        f.name,
                        chunks,
                        session_id=st.session_state.session_id,
                        file_data=_file_bytes_for_storage(f),
                        file_size=f.size,
                    )
                    if n == 0:
                        st.info(f"《{f.name}》已入库，跳过。如需重新处理，请先删除它。")
                        continue
                    st.success(f"《{f.name}》入库完成：{n} 个片段（已持久化，重启不丢失）")
                except Exception:
                    logger.exception("《%s》入库失败", f.name)
                    st.error(f"《{f.name}》入库失败，请稍后重试。")
                    continue

                # v3.3：会话还没创建（首次提问才会创建）时先记下，创建后立即绑定
                if not st.session_state.session_id:
                    doc = db.get_document_by_name(USER_ID, f.name)
                    if doc:
                        st.session_state.pending_doc_ids.append(doc["id"])

                # 入库后自动生成全文摘要 + 3 个推荐问题（只在该文档内检索），
                # 生成结果持久化到文档上，随会话/文档恢复
                if not api_key:
                    st.info(f"《{f.name}》已入库；填写 API Key 后可自动生成摘要与推荐问题。")
                    continue
                try:
                    ctx = similarity_search(
                        USER_ID,
                        "本文的主要内容、核心观点、结论与关键信息是什么",
                        top_k=6,
                        source=f.name,
                    )
                    brief = generate_doc_brief(f.name, ctx, api_key=api_key)
                    st.session_state.doc_briefs[f.name] = brief
                    save_document_summary(USER_ID, f.name, brief)  # 持久化
                except QAFriendlyError as e:
                    st.warning(f"《{f.name}》摘要生成失败：{e}")
                except Exception:
                    logger.exception("《%s》摘要生成失败", f.name)
                    st.warning(f"《{f.name}》已入库，但摘要生成失败，可稍后重试。")

    st.divider()
    docs = list_document_details(USER_ID)
    if not docs:
        st.caption("知识库还是空的，先上传一个 PDF 吧。")
        return

    # 当前会话绑定的文档（列表里打 ✅ 标记）
    bound_ids = set(st.session_state.get("session_doc_ids", [])) | set(
        st.session_state.get("pending_doc_ids", [])
    )
    st.markdown("**已入库文档**（持久保存在数据库中，重启应用不会丢失）")
    for d in docs:
        name = d["filename"]
        icon = "📊" if d.get("doc_type") == "pptx" else "📄"
        in_session = "✅ " if d["id"] in bound_ids else ""
        size_mb = (d.get("file_size") or 0) / (1024 * 1024)
        cols = st.columns([0.72, 0.28])
        cols[0].write(
            f"{icon} {in_session}{name} · {d.get('chunk_count', 0)} 片段"
            + (f" · {size_mb:.1f}MB" if size_mb >= 0.1 else "")
        )
        if cols[1].button("删除", key=f"del_doc_{name}"):
            # 只记录待删目标，弹窗确认后才真正删除
            st.session_state.pending_delete_doc = {"id": d["id"], "name": name}

        # BUG-10：展示入库后的实际切分文本，便于核对断字与 OCR 质量。
        preview = db.list_document_chunks_preview(USER_ID, d["id"], limit=3)
        total = d.get("chunk_count", 0)
        if preview:
            with st.expander(
                f"🔍 《{name}》片段预览（前 {len(preview)} / {total} 段）",
                expanded=False,
            ):
                st.caption("以下为入库后的实际片段文本；扫描件展示的是 OCR 后的结果。")
                for i, chunk in enumerate(preview, start=1):
                    st.markdown(f"**片段 {i} · 第 {chunk['page']} 页**")
                    st.text(chunk["text"])

    if st.session_state.get("pending_delete_doc"):
        _confirm_delete_document()


# ---------- 页面三：⚙️ 设置 ----------
def page_settings():
    """设置页：账号信息、API Key 管理（加密 + 掩码）、修改密码。"""
    st.markdown('<div class="title-divider"></div>', unsafe_allow_html=True)
    st.subheader("⚙️ 设置")

    user = db.get_user_by_id(USER_ID) or {}
    created = user.get("created_at")
    created_str = created.strftime("%Y-%m-%d %H:%M") if created else "-"

    st.markdown("**账号信息**")
    info_cols = st.columns(2)
    info_cols[0].text_input("用户名", value=st.session_state.user["username"], disabled=True)
    info_cols[1].text_input("注册时间", value=created_str, disabled=True)

    st.divider()
    st.markdown("**DeepSeek API Key**")
    current_key = _current_api_key()
    st.code(auth.mask_key(current_key), language=None)
    st.caption(
        "Key 使用 Fernet 加密后保存，只有你自己能用。"
        "申请地址：[platform.deepseek.com](https://platform.deepseek.com/)"
    )

    with st.form("api_key_form"):
        new_key = st.text_input(
            "填入新的 Key", type="password", placeholder="sk-...", help="保存后会覆盖旧 Key"
        )
        saved = st.form_submit_button("保存 Key", type="primary")
    if saved:
        try:
            # 先校验格式，再调一次轻量鉴权接口（GET /models，不消耗 token），
            # 两步都通过才加密落库，避免把坏 Key 存进去。
            ok, msg = auth.validate_api_key_format(new_key)
            if not ok:
                raise auth.AuthFriendlyError(msg)
            with st.spinner("正在验证 Key 是否有效…"):
                verify_api_key(new_key)
            masked = auth.set_user_api_key(USER_ID, new_key)
            st.success(f"Key 已验证并加密保存（{masked}）")
            st.rerun()
        except (auth.AuthFriendlyError, QAFriendlyError) as e:
            st.error(str(e))

    if current_key and st.button("删除已保存的 Key"):
        auth.delete_user_api_key(USER_ID)
        st.success("已删除保存的 Key，需要时可重新填写。")
        st.rerun()

    st.divider()
    st.markdown("**修改密码**")
    with st.form("password_form"):
        old_password = st.text_input("当前密码", type="password")
        new_password = st.text_input("新密码", type="password", help="至少 8 位")
        confirm_password = st.text_input("确认新密码", type="password")
        changed = st.form_submit_button("修改密码")
    if changed:
        if new_password != confirm_password:
            st.error("两次输入的新密码不一致。")
        else:
            ok, msg = auth.change_password(USER_ID, old_password, new_password)
            if ok:
                st.success(msg)
                st.session_state.user = None  # 修改成功后要求重新登录
                _reset_chat_state()
                st.rerun()
            else:
                st.error(msg)


# ---------- 多页面导航 ----------
PAGES = [
    st.Page(page_chat, title="问答", icon="💬"),
    st.Page(page_documents, title="我的文档", icon="📁"),
    st.Page(page_settings, title="设置", icon="⚙️"),
]
navigation = st.navigation(PAGES)
navigation.run()
