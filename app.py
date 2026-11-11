# -*- coding: utf-8 -*-
"""
app.py（v3.7 外观改版）
职责：Streamlit 界面（单一职责：只做交互与展示，业务逻辑在 auth/db/loader/vectorstore/qa）。
- v3 变更：新增账号体系（注册/登录）、session_state 保存当前用户；
          知识库与会话按 user_id 隔离；所有用户统一使用平台 DeepSeek Key。
- v3.1 变更：问答支持多轮对话上下文（history 传入 qa 层）；
          文档页支持 PDF 与 PPTX 上传（按后缀分发解析）；
          图片型 PDF 走 OCR 兜底，识别失败的页码用一条提示列出；
          来源展示按类型区分「第 X 页」/「第 X 张」。
- v3.2 变更：文档页新增「强制全文 OCR」开关，解决公式类 PDF 提取不出公式的问题。
- v3.3 变更（持久化）：文档与片段存数据库（Chroma 退役），云端重启后不丢；
          新增会话-文档绑定：加载旧会话恢复其文档列表与摘要；
          问答检索默认限定在本会话选定的文档内。
- v3.4 变更（P3 体验层）：提问后先显示「思考中」占位，首字出现即消失；
          解析时显示真实阶段进度（抽取 / OCR / 入库 / 摘要）与耗时预期；
          应用内首屏加载骨架（平台休眠页不可改，见 README/交付说明）；
          展示实际调用模型、每轮 token 用量与估算费用（问答与摘要）。
- v3.7 变更（仅外观）：浅色学习风主题、900px 居中阅读区、顶部品牌栏、
          上传虚线卡片、左右对话气泡、摘要卡与引用出处 pill；业务逻辑与接口不变。
- v3.8 变更（平台 Key + 额度）：注册后默认使用 Secrets 里的平台官方 Key，
           新增 free/paid 每日额度与 topbar 真实剩余；
           上传前拦截超 100 页 / 20MB 的单文件。
- v3.10 变更：删除用户自填 Key 入口与用户 Key 优先逻辑，所有用户只走平台 Key；
           历史库里保存的用户 Key 由启动迁移一次性置空。
- v3.11 变更：登录改为邮箱验证码为主入口，新邮箱自动建号；自动建号用户
           首次进入时可跳过资料引导，之后在设置页修改用户名或设置密码。
- 页面结构（st.navigation 多页面）：
  1) 💬 问答      —— 聊天、流式回答、引用原文与相似度、文档摘要与推荐问题、会话管理
  2) 📁 我的文档  —— 上传 PDF / PPTX 入库（查重、存库）、已入库列表与删除
  3) ⚙️ 设置      —— 账号信息、修改用户名、设置/修改密码；不提供 API Key 入口
会话数据存放在数据库（见 db.py），不再使用本地 JSON 文件。
运行方式：streamlit run app.py
"""
import logging
import os
from contextlib import contextmanager

import streamlit as st

# 原始文件字节入库上限：超过则不存原始文件（片段与摘要仍会持久化），
# 避免撑爆 Supabase 免费版数据库（500MB）
MAX_FILE_BYTES = 20 * 1024 * 1024

# 提问输入上限（BUG-08）：显示「已输入 / 上限」，超过上限时提示并禁止提交
MAX_QUESTION_CHARS = 2000

st.set_page_config(
    page_title="课程资料问答助手",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# P3：首屏加载骨架。Streamlit Cloud 的「休眠唤醒」页由平台接管、应用层改不了；
# 这里保证脚本一开始执行就先给出可见提示，覆盖后面 torch / sentence-transformers
# 等重依赖的 import 时间（这些 import 原本发生在任何 st 输出之前，用户看到的是空白）。
# 只在本次会话首次运行时显示：随后的每次 rerun 不再闪一下全屏加载提示。
_boot_placeholder = None
if "app_booted" not in st.session_state:
    _boot_placeholder = st.empty()
    _boot_placeholder.markdown(
        """
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
                    min-height:52vh;gap:12px;color:#6B7280;">
          <div style="font-size:40px">📄</div>
          <div style="font-size:18px;font-weight:600;color:#374151">正在加载课程资料问答助手…</div>
          <div style="font-size:13px">首次唤醒通常需要 10–20 秒，请稍候；之后会明显更快。</div>
          <div style="width:220px;height:4px;border-radius:2px;background:#E5E7EB;overflow:hidden">
            <div style="width:45%;height:100%;background:#2563EB;border-radius:2px"></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------- 云端适配：把 Streamlit Secrets 同步进环境变量 ----------
def _sync_secrets_to_env():
    """
    Streamlit Cloud 上配置的 Secrets 不一定自动出现在 os.environ 里，
    而 db.py / auth.py 是读环境变量的。这里显式同步一次，
    保证「本地 .env」和「云端 Secrets」两种部署方式走同一套读取逻辑。
    """
    try:
        for key in (
            "DATABASE_URL",
            "APP_SECRET_KEY",
            "DEEPSEEK_API_KEY",
            "TURNSTILE_SITE_KEY",
            "TURNSTILE_SECRET_KEY",
            "SMTP_HOST",
            "SMTP_PORT",
            "SMTP_USER",
            "SMTP_PASSWORD",
            "SMTP_FROM",
            "SMTP_SECURITY",
            "SMTP_TIMEOUT",
        ):
            if not os.environ.get(key):  # 已存在的环境变量不覆盖
                value = st.secrets.get(key, "")
                if value:
                    os.environ[key] = str(value)
    except Exception:
        pass  # 本地没有 secrets.toml 时访问 st.secrets 会抛异常，忽略即可


_sync_secrets_to_env()

# 重依赖放在骨架屏之后 import（延迟首屏空白的开始时间，P3 冷启动优化）；
# noqa 注释是刻意保留的：这里不能把这些 import 提到文件顶部。
import build_guard  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import quota  # noqa: E402
import request_ip  # noqa: E402
import turnstile  # noqa: E402
from loader import (  # noqa: E402
    FileParseError,
    check_file_limits,
    load_and_split_file,
    repair_chunk_text,
)
from qa import (  # noqa: E402
    MODEL,
    QAFriendlyError,
    format_usage,
    generate_doc_brief,
    stream_answer,
)
from ui_helpers import (  # noqa: E402
    ask_banner_text,
    escape_markdown,
    mark_ask_pending,
    parse_progress_fraction,
    parse_progress_label,
    reveal_on_first_chunk,
)
from ui_theme import inject_theme, render_topbar  # noqa: E402
from vectorstore import (  # noqa: E402
    add_document,
    delete_document,
    document_exists,
    list_document_details,
    save_document_summary,
    similarity_search,
)

logger = logging.getLogger(__name__)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _verify_loaded_modules() -> None:
    """检测 Streamlit 热重载后仍加载旧模块的情况，避免修复静默失效。"""
    stale = build_guard.stale_modules(
        {
            "auth": (auth, os.path.join(_APP_DIR, "auth.py")),
            "db": (db, os.path.join(_APP_DIR, "db.py")),
            "build_guard": (build_guard, os.path.join(_APP_DIR, "build_guard.py")),
        }
    )
    if not stale:
        return
    logger.error(
        "检测到未重新加载的旧模块（%s），当前进程仍在执行旧代码；"
        "需要在 Streamlit Cloud 执行 Reboot app 或完整重新部署。",
        ", ".join(stale),
    )
    st.error(
        "服务端代码未完成更新，登录模块仍是旧版本，暂时无法登录。"
        "请管理员在 Streamlit Cloud 重新部署（Reboot app）后再试。"
    )
    st.stop()


_verify_loaded_modules()

try:
    db.init_db()
except Exception as e:
    if _boot_placeholder is not None:
        _boot_placeholder.empty()
    st.error(f"数据库连接失败：{e}")
    st.stop()

if _boot_placeholder is not None:
    _boot_placeholder.empty()
    st.session_state["app_booted"] = True


# ---------- 外观主题：只改样式，不改变任何业务逻辑 ----------
inject_theme()


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


# ---------- 登录页：邮箱验证码 / 账号密码 ----------
def render_auth_page():
    """未登录时展示的居中登录卡片。"""
    left, mid, right = st.columns([1, 1.6, 1])
    with mid:
        st.markdown(
            '<div class="wb-auth-head"><div class="wb-brand">'
            '<span class="wb-brand-mark">📄</span>课程资料问答助手</div></div>'
            '<div class="wb-auth-sub">上传 PDF → 自动摘要与推荐问题 → 流式问答，'
            "答案附带原文出处与相似度</div>",
            unsafe_allow_html=True,
        )

        # 两种登录方式；邮箱验证码是默认入口，未注册邮箱会自动创建账号。
        with st.container(border=True, key="auth_card"):
            modes = ("邮箱验证码登录", "账号密码登录")
            if st.session_state.pop("_switch_to_login", False):
                st.session_state["auth_mode"] = modes[0]
            elif st.session_state.get("auth_mode") not in modes:
                # 兼容旧会话里遗留的「登录 / 注册」选项。
                st.session_state["auth_mode"] = modes[0]
            mode = st.radio(
                "登录方式",
                modes,
                horizontal=True,
                label_visibility="collapsed",
                key="auth_mode",
            )
            notice = st.session_state.pop("auth_notice", None)
            if notice:
                st.success(notice)
            if mode == "邮箱验证码登录":
                render_email_code_login_form()
            else:
                render_password_login_form()


def _set_logged_in_user(user: dict) -> None:
    """所有登录入口共用：写入当前用户并清理上一个账号的聊天状态。"""
    st.session_state.user = {"id": user["id"], "username": user["username"]}
    st.session_state.pop("email_login_email", None)
    st.session_state.pop("email_login_notice", None)
    st.session_state.pop("_onboarding_dialog_dismissed", None)
    _reset_chat_state()


def render_password_login_form():
    """登录表单：失败统一提示「用户名或密码不正确」，连续失败按账号临时锁定。"""
    # L-05：锁定时把剩余时间直接显示在表单上方，避免用户反复试密码
    locked_remaining = auth.get_login_lock_remaining(
        st.session_state.get("login_username", "")
    )
    if locked_remaining > 0:
        st.warning(
            "该账号连续登录失败次数过多，已临时锁定，请在 "
            f"{auth.format_lock_remaining(locked_remaining)} 后重试。"
        )
    with st.form("login_form"):
        username = st.text_input(
            "用户名或邮箱",
            placeholder="输入用户名或注册邮箱",
            help="用户名 3–20 位字母、数字或下划线；也可以输入注册时使用的完整邮箱地址。",
            key="login_username",
        )
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", use_container_width=True, type="primary")
    if submitted:
        remaining = auth.get_login_lock_remaining(username)
        if remaining > 0:
            # 锁定期间的任何尝试都拒绝；上方警告已给出剩余时间
            return
        try:
            user = auth.login(username, password)
        except auth.AuthFriendlyError as e:
            st.error(str(e))
            return
        except Exception as e:
            st.error(f"登录失败：{e}")
            return
        if user:
            auth.clear_login_failures(username)
            _set_logged_in_user(user)
            st.rerun()
        else:
            locked = auth.record_login_failure(username)
            if locked:
                st.error(
                    f"连续 {auth.LOGIN_MAX_FAILURES} 次登录失败，账号已锁定，请在 "
                    f"{auth.format_lock_remaining(locked)} 后重试。"
                )
            else:
                st.error("用户名或密码不正确")


@st.cache_resource(show_spinner=False)
def _get_turnstile_component():
    """登录/注册流程共用的本地 Turnstile 自定义组件（不引入额外 pip 依赖）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "components", "turnstile")
    return st.components.v1.declare_component("turnstile_guard", path=path)


def _reset_turnstile_token():
    """验证失败后清掉旧 token，并换一个 widget key 让前端重新拉取验证码。"""
    st.session_state.pop("turnstile_token", None)
    st.session_state["_turnstile_version"] = (
        st.session_state.get("_turnstile_version", 0) + 1
    )


def _render_turnstile() -> str:
    """渲染 Turnstile，返回当前有效的客户端 token（拿不到时为空字符串）。"""
    site_key = turnstile.site_key()
    if not site_key:
        st.warning(
            "人机验证未配置，暂时无法发送验证码。请管理员在 Streamlit Secrets 中配置 "
            "TURNSTILE_SITE_KEY 和 TURNSTILE_SECRET_KEY。"
        )
        return ""
    version = st.session_state.get("_turnstile_version", 0)
    widget_token = _get_turnstile_component()(
        sitekey=site_key,
        theme="light",
        key=f"turnstile_widget_{version}",
    )
    if widget_token:
        st.session_state["turnstile_token"] = str(widget_token)
    elif widget_token == "":
        # 组件在 token 过期时会回传空字符串，同步清掉服务端 session_state。
        st.session_state.pop("turnstile_token", None)
    token = st.session_state.get("turnstile_token", "")
    if token:
        st.caption("人机验证已完成。")
    else:
        st.caption("请先完成上方人机验证，再获取验证码。")
    return token


def render_email_code_login_form():
    """邮箱验证码登录：新邮箱自动建号，老邮箱直接登录。"""
    pending_email = st.session_state.get("email_login_email", "")
    if pending_email:
        notice = st.session_state.pop("email_login_notice", None)
        if notice:
            st.success(notice)
        st.caption(
            f"验证码已发送至 {pending_email}，10 分钟内有效。"
            "新邮箱完成验证后会自动创建账号。"
        )
        with st.form("email_code_login_form"):
            verification_code = st.text_input(
                "邮箱验证码",
                placeholder="6 位数字",
                max_chars=auth.EMAIL_CODE_LENGTH,
                key="login_email_code",
            )
            submitted = st.form_submit_button(
                "登录", use_container_width=True, type="primary"
            )
            change_email = st.form_submit_button(
                "更换邮箱", use_container_width=True
            )

        if change_email:
            st.session_state.pop("email_login_email", None)
            st.session_state.pop("email_login_notice", None)
            st.session_state.pop("login_email_code", None)
            st.rerun()

        if submitted:
            result = auth.login_with_email_code(
                pending_email,
                verification_code,
                client_ip=request_ip.get_client_ip(),
            )
            if result["ok"]:
                _set_logged_in_user(result["user"])
                st.rerun()
            else:
                st.error(result["msg"])
        return

    st.caption("输入邮箱获取 6 位验证码；未注册的邮箱会自动创建账号。")
    st.markdown("**先完成人机验证**")
    turnstile_token = _render_turnstile()
    with st.form("email_code_send_form"):
        email = st.text_input(
            "邮箱",
            placeholder="用于接收登录验证码",
            key="email_login_input",
        )
        send_code = st.form_submit_button(
            "发送验证码",
            use_container_width=True,
            disabled=not turnstile_token,
        )

    if send_code:
        result = auth.request_email_login_code(
            email,
            turnstile_token,
            client_ip=request_ip.get_client_ip(),
        )
        if result["ok"]:
            st.session_state["email_login_email"] = result["email"]
            st.session_state["email_login_notice"] = result["msg"]
            _reset_turnstile_token()
            st.rerun()
        else:
            st.error(result["msg"])
            _reset_turnstile_token()


def _mark_onboarding_dismissed():
    """用户点关闭时只在本会话内停止重复弹出，资料仍可在设置页补全。"""
    user = st.session_state.get("user") or {}
    if user.get("id"):
        st.session_state["_onboarding_dialog_dismissed"] = str(user["id"])


@st.dialog("完善账号信息", on_dismiss=_mark_onboarding_dismissed)
def _render_onboarding_dialog(user: dict):
    """自动建号后的首次引导；用户名和密码都可以直接跳过。"""
    st.caption(
        "可以现在起一个用户名并设置密码，也可以先跳过。"
        "临时用户名和密码后续都能在「设置」页修改。"
    )
    with st.form("onboarding_form"):
        username = st.text_input(
            "用户名（可选）",
            placeholder="3–20 位字母、数字或下划线",
            key="onboarding_username",
        )
        password = st.text_input(
            "密码（可选）",
            type="password",
            help="至少 8 位；不设置就只能用邮箱验证码登录",
            key="onboarding_password",
        )
        confirm_password = st.text_input(
            "确认密码",
            type="password",
            key="onboarding_password_confirm",
        )
        save = st.form_submit_button(
            "保存并继续", use_container_width=True, type="primary"
        )
        skip = st.form_submit_button(
            "暂时跳过（保留临时用户名）", use_container_width=True
        )

    if skip:
        result = auth.finish_onboarding(USER_ID)
        if not result["ok"]:
            st.error(result["msg"])
            return
        st.session_state.user = {
            "id": result["user"]["id"],
            "username": result["user"]["username"],
        }
        st.session_state["_app_notice"] = result["msg"]
        st.rerun()

    if save:
        result = auth.complete_onboarding(
            USER_ID,
            username or user["username"],
            password=password,
            confirm_password=confirm_password,
        )
        if not result["ok"]:
            st.error(result["msg"])
            return
        st.session_state.user = {
            "id": result["user"]["id"],
            "username": result["user"]["username"],
        }
        st.session_state["_app_notice"] = result["msg"]
        st.rerun()


def _maybe_render_onboarding() -> None:
    """自动建号账号首次进入时弹出一次；关闭或跳过都不会反复打扰。"""
    user = db.get_user_by_id(USER_ID)
    if not user or not user.get("is_auto_created"):
        return
    if user.get("onboarding_completed_at") is not None:
        return
    if st.session_state.get("_onboarding_dialog_dismissed") == str(USER_ID):
        return
    _render_onboarding_dialog(user)


def _maybe_show_password_reminder() -> None:
    """跳过密码后只提醒一次；设置页入口则始终保留。"""
    user = db.get_user_by_id(USER_ID)
    if not user or not user.get("is_auto_created"):
        return
    if user.get("onboarding_completed_at") is None:
        return
    if user.get("has_password") or user.get("password_reminder_seen_at"):
        return
    st.warning(
        "你还没有设置密码，目前只能使用邮箱验证码登录。"
        "需要用户名 + 密码登录时，请到「设置」页设置密码。"
    )
    db.mark_password_reminder_seen(USER_ID)


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
        if st.button("退出登录", use_container_width=True, key="logout_btn"):
            st.session_state.user = None
            st.session_state["_switch_to_login"] = True
            _reset_chat_state()
            st.rerun()
        st.divider()


# 未登录：只显示登录页（st.stop 阻断后面所有内容）
if st.session_state.user is None:
    render_auth_page()
    st.stop()

USER_ID = st.session_state.user["id"]
# 每次都从库里读一次等级，管理员手动改成 paid 后无需重新登录即可生效。
USER = db.get_user_by_id(USER_ID) or st.session_state.user
if not auth.is_email_verified(USER):
    # 未验证邮箱的账号不能进入任何业务页面，只能回到登录页。
    st.session_state.user = None
    _reset_chat_state()
    st.session_state.auth_notice = "该账号尚未完成邮箱验证，请使用邮箱验证码登录完成验证。"
    st.rerun()

render_sidebar_user()

_app_notice = st.session_state.pop("_app_notice", None)
if _app_notice:
    st.success(_app_notice)

_maybe_render_onboarding()
_maybe_show_password_reminder()

TIER = quota.normalize_tier(USER.get("tier"))
CLIENT_IP = request_ip.get_client_ip()
IP_HASH = request_ip.hash_ip(CLIENT_IP)

# 顶栏先渲染一次，页面执行完再按最新用量刷新：提问成功后的 rerun 与
# 上传入库这类不触发 rerun 的操作，都能让顶栏数字及时减少。
_quota_topbar_slot = st.empty()


def _refresh_quota_topbar() -> None:
    tier = quota.normalize_tier((db.get_user_by_id(USER_ID) or USER).get("tier"))
    snap = quota.snapshot(USER_ID, tier, ip_hash=IP_HASH)
    with _quota_topbar_slot:
        render_topbar(
            quota_value=quota.topbar_text(snap),
            quota_hint=quota.topbar_hint(snap),
        )


_refresh_quota_topbar()


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

    st.write(f"确定要删除《{escape_markdown(name)}》吗？")
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
                f"已删除《{escape_markdown(name)}》（{removed} 个片段），可重新上传。"
                if removed
                else f"《{escape_markdown(name)}》已不存在。"
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
    """
    平台统一 Key：只从 Streamlit Secrets / 环境变量 / 本地 .env 读取，
    不落日志、不在页面展示，也不再读取任何用户自填 Key。
    """
    return auth.get_platform_api_key()


def _require_key_or_guide() -> str:
    """取平台 Key；未配置时给中文提示，不抛异常。"""
    key = _current_api_key()
    if not key:
        st.warning("平台 DeepSeek Key 暂未配置，暂时无法提问，请联系管理员。")
    return key


# ---------- 通用组件 ----------
def render_sources(sources: list[dict]):
    """用 [1][2] 小 pill 展示引用出处，点击展开原文与相似度（PDF 页 / PPTX 张）。"""
    if not sources:
        return
    st.markdown('<div class="wb-source-label">引用出处</div>', unsafe_allow_html=True)
    with st.container(horizontal=True, gap="small", wrap=True):
        for i, s in enumerate(sources, start=1):
            # Chroma 返回的是余弦距离（越小越相似），换算成百分比相似度展示
            sim = max(0.0, 1.0 - float(s["score"])) * 100
            unit = "张" if s.get("doc_type") == "pptx" else "页"
            with st.popover(f"[{i}]"):
                st.markdown(
                    f"**{escape_markdown(s['source'])}** · 第 {s['page']} {unit} · "
                    f"相似度 **{sim:.1f}%**"
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


def _file_unit(filename: str) -> str:
    """解析进度里的页数单位：PPTX 用「张」，PDF 用「页」。"""
    return "张" if os.path.splitext(filename)[1].lower() == ".pptx" else "页"


@contextmanager
def _parse_progress(filename: str):
    """
    P3：解析进度上下文。
    - 进度值按真实页数推进（抽取 5%–65%，OCR 70%–90%），不是假进度条；
    - 同时给出耗时预期；退出时（成功/失败/跳过）自动清掉进度与提示。
    """
    bar = st.progress(0.0, text=f"准备解析《{escape_markdown(filename)}》…")
    hint = st.caption(
        "解析通常需要 10 秒左右，大文件或扫描页更多时会更久；"
        "下方进度按实际页数推进。"
    )

    def on_progress(stage, done, total):
        bar.progress(
            parse_progress_fraction(stage, done, total),
            text=parse_progress_label(stage, done, total, _file_unit(filename)),
        )

    try:
        yield bar, on_progress
    finally:
        bar.empty()
        hint.empty()


# ---------- 页面一：💬 问答 ----------
def _on_send_click():
    """发送按钮的 on_click 回调：重跑前先记下待提问内容。"""
    mark_ask_pending(st.session_state, st.session_state.get("question_input"))


def page_chat():
    """问答页：聊天窗口、流式回答、引用来源、摘要推荐问题、会话管理。"""
    # P3：点击「发送」后 Streamlit 要重跑整个脚本（线上约 3–4 秒）才会渲染到聊天区，
    # 这段时间页面顶部先给出「思考中」，避免看起来没反应。
    _ask_banner = None
    _pending_question = st.session_state.pop("ask_pending", None)
    if _pending_question:
        _ask_banner = st.empty()
        _ask_banner.markdown(ask_banner_text(_pending_question))

    # 上一轮成功提交后清空输入框：必须在 text_area 实例化之前修改 key
    if st.session_state.pop("_clear_question_input", False):
        st.session_state["question_input"] = ""

    # 会话管理（数据来自数据库，只查当前用户的）
    with st.sidebar:
        st.subheader("💬 会话管理")
        if st.button("➕ 新建会话", use_container_width=True, key="new_session_btn"):
            _reset_chat_state()
            st.rerun()

        sessions = db.list_sessions(USER_ID)
        if not sessions:
            st.caption("暂无历史会话")
        for s in sessions:
            cols = st.columns([0.52, 0.24, 0.24])
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
    # OBS-02/03：直接用上面已查到的 all_docs，不再按文档逐个查库（去掉 N+1）
    summaries = {d["filename"]: d.get("summary") for d in all_docs}
    for name in scope_names:
        brief = st.session_state.doc_briefs.get(name)
        if brief is None:
            brief = summaries.get(name)
        if brief:
            st.session_state.doc_briefs[name] = brief

    # 主区：上次入库生成的摘要与推荐问题（浅灰卡 + 左侧蓝色竖线）
    for idx, (name, brief) in enumerate(st.session_state.doc_briefs.items()):
        with st.container(border=True, key=f"summary_card_{idx}"):
            st.markdown(f"**📄 《{escape_markdown(name)}》 · 全文摘要**")
            st.markdown(brief.get("summary", ""))
            if brief.get("usage"):
                st.caption("📊 摘要生成 · " + format_usage(brief["usage"]))
            questions = (brief.get("questions") or [])[:3]
            if questions:
                st.markdown(
                    '<div class="wb-suggest-label">推荐问题</div>',
                    unsafe_allow_html=True,
                )
                for i, q in enumerate(questions):
                    if st.button(
                        q, key=f"sug_{name}_{i}", use_container_width=True
                    ):
                        st.session_state.pending_question = q

    st.markdown(
        f"#### 💬 当前会话：{escape_markdown(st.session_state.session_title)}"
    )
    # P3：让用户随时看到当前请求的模型名；每轮回答后另附 API 实际返回的用量与估算费用。
    st.caption(
        "回答模型：deepseek-flash"
        " · token 用量来自 API 返回的 usage，费用为官方价目估算"
    )

    # 空状态：居中提示 + 示例文档入口
    if not st.session_state.messages:
        if all_docs:
            empty_title = "可以开始提问了"
            empty_text = (
                "当前知识库已有文档。在下方输入问题，或先查看上方的文档摘要与推荐问题。"
            )
            link_label = "管理我的文档"
        else:
            empty_title = "从一份课程文档开始"
            empty_text = (
                "上传 PDF 或 PPTX，自动生成摘要与推荐问题；提问后答案会标注原文出处，"
                "点击 [1] 可查看原文片段。"
            )
            link_label = "上传示例文档"
        with st.container(key="empty_state"):
            st.markdown(
                '<div class="wb-empty"><div class="wb-empty-icon">💬</div>'
                f"<h3>{empty_title}</h3><p>{empty_text}</p></div>",
                unsafe_allow_html=True,
            )
            with st.container(key="empty_action"):
                st.page_link(PAGES[1], label=link_label, icon="📁")
    else:
        # 渲染历史消息（含引用来源）
        with st.container(key="chat_thread"):
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
                    if msg.get("usage"):
                        st.caption("📊 " + format_usage(msg["usage"]))
                    if msg.get("sources"):
                        render_sources(msg["sources"])

    # BUG-08：输入框下方实时显示「已输入 / 上限」，超过上限时提示并禁用发送。
    with st.container(key="chat_composer"):
        input_col, button_col = st.columns(
            [0.85, 0.15], vertical_alignment="bottom"
        )
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
            on_click=_on_send_click,
            key="send_btn",
        )
    if submitted:
        typed = (typed or "").strip()
        if not typed:
            st.warning("请输入问题后再发送。")
        elif len(typed) > MAX_QUESTION_CHARS:
            st.warning(f"问题不能超过 {MAX_QUESTION_CHARS} 字，请缩短后重试。")
        else:
            # 聊天区立刻会渲染「思考中」占位，顶部临时提示到此交接，避免重复
            # 也避免中间出现没有任何反馈的空窗。
            if _ask_banner is not None:
                _ask_banner.empty()
            if handle_question(typed):
                st.session_state._clear_question_input = True
                st.rerun()

    # 点击推荐问题提问（在同一轮运行中消费 pending_question）
    if st.session_state.pending_question:
        q = st.session_state.pending_question
        st.session_state.pending_question = None
        handle_question(q)

    # 兜底：任何路径都没走到交接点时，本轮结束前也要清掉顶部提示，避免残留。
    if _ask_banner is not None:
        _ask_banner.empty()


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
    """处理一次提问：取平台 Key → 检索 → 带历史流式生成 → 展示来源 → 自动保存会话。"""
    # 调用前先查当天额度：超限直接中文拦截，不创建会话、不调 API、不记用量。
    snap = quota.snapshot(USER_ID, TIER, ip_hash=IP_HASH)
    allowed, quota_msg = quota.check_question(snap)
    if not allowed:
        st.warning(quota_msg)
        return False

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
    contexts, answer, usage = [], None, {}
    error_text = ""
    with st.chat_message("assistant"):
        # P3：提交后立刻显示「思考中」；首字出现或出错时清掉，避免一直转圈。
        thinking = st.empty()
        thinking.markdown(
            "⏳ **思考中…** 正在检索相关资料并等待模型响应（首字通常 3–5 秒）"
        )
        try:
            contexts = similarity_search(
                USER_ID, question, top_k=4, sources=scope or None
            )
            thinking.markdown("⏳ **思考中…** 已找到相关资料，正在生成回答…")
            # 真正发起模型调用前记 1 次；被上面拦截的请求不会走到这里。
            quota.record_question(USER_ID)
            # write_stream 会逐段消费生成器，实现打字机效果；
            # reveal_on_first_chunk 保证首个 chunk 到达即清掉占位、异常时也不残留。
            answer = st.write_stream(
                reveal_on_first_chunk(
                    stream_answer(
                        question,
                        contexts,
                        history=history,
                        usage_out=usage,
                    ),
                    on_first=thinking.empty,
                    on_finish=thinking.empty,
                )
            )
        except QAFriendlyError as e:
            thinking.empty()
            error_text = str(e)
            st.warning(error_text)
        except Exception:
            thinking.empty()
            logger.exception("生成回答失败")
            error_text = "生成回答时出现未预期的错误，请稍后重试。"
            st.error(error_text)
        finally:
            thinking.empty()

        if answer is not None:
            # P3：展示本轮 API 实际返回的 token 用量与估算费用（无用量则不显示）。
            if usage.get("total_tokens"):
                st.caption("📊 " + format_usage(usage))
            render_sources(contexts)
            message = {"role": "assistant", "content": answer, "sources": contexts}
            if usage.get("total_tokens"):
                message["usage"] = usage
            st.session_state.messages.append(message)
        else:
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": "⚠️ "
                    + (error_text or "本次未生成回答，请检查 API Key 或网络后重试。"),
                }
            )
    save_current_session()
    # 失败时不触发 rerun：否则刚显示的中文错误会被刷新掉，用户看不到原因。
    return answer is not None


# ---------- 页面二：📁 我的文档 ----------
def page_documents():
    """文档页：多文件上传入库（PDF / PPTX，查重）、已入库列表、逐文档删除。"""
    st.markdown("## 📁 我的文档")
    st.caption(
        "支持 PDF（含扫描件，图片页会自动 OCR 识别）与 PPTX；"
        "上传的文档只会进入你自己的知识库，其他用户看不到。"
    )
    flash = st.session_state.pop("doc_flash", None)
    if flash:
        st.success(flash)

    parse_clicked = False
    with st.container(border=True, key="upload_card"):
        st.markdown("#### 上传文档")
        st.caption("支持 PDF / PPTX，可多选；上传后自动解析并入库到你的个人知识库。")
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
            names = "、".join(
                f"《{escape_markdown(f.name)}》" for f in unsupported_uploads
            )
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
            parse_clicked = st.button(
                "解析并入库", type="primary", key="upload_submit_btn"
            )

    if uploaded_files and parse_clicked:
        api_key = _current_api_key()  # 平台 Key；没有则只入库、不生成摘要
        for f in uploaded_files:
            if os.path.splitext(f.name)[1].lower() not in (".pdf", ".pptx"):
                st.warning(f"《{escape_markdown(f.name)}》格式不支持，仅支持 PDF 与 PPTX，已跳过。")
                continue
            if document_exists(USER_ID, f.name):
                st.info(f"《{escape_markdown(f.name)}》已入库，跳过。如需重新处理，请先删除它。")
                continue

            # 单文件硬限制：先读大小与页数，超限直接中文拒绝，不解析、不扣额度。
            try:
                check_file_limits(f, f.name, size_bytes=getattr(f, "size", None))
            except FileParseError as e:
                st.error(f"《{escape_markdown(f.name)}》{e}")
                continue

            # 调用前查额度：文档总数与今日新传次数；超限不解析、不写库。
            snap = quota.snapshot(USER_ID, TIER, ip_hash=IP_HASH)
            allowed, quota_msg = quota.check_upload(snap)
            if not allowed:
                st.warning(quota_msg)
                continue

            with _parse_progress(f.name) as (progress_bar, on_progress):
                try:
                    # 按后缀分发解析；OCR 情况写进 report，供下方提示使用
                    ocr_report = {}
                    chunks = load_and_split_file(
                        f,
                        f.name,
                        ocr_report=ocr_report,
                        force_ocr=force_ocr,
                        on_progress=on_progress,
                    )
                except FileParseError as e:
                    st.error(f"《{escape_markdown(f.name)}》解析失败：{e}")
                    continue
                except Exception:
                    # 底层异常原文只进日志，界面只给可操作的中文提示
                    logger.exception("解析《%s》失败", f.name)
                    st.error(f"《{escape_markdown(f.name)}》解析失败：文件无法读取，请确认文件完好后重试。")
                    continue

                if not chunks:
                    # 只有全文完全没有可用文本时才这样提示（单个图片页失败另有提示）
                    st.warning(f"《{escape_markdown(f.name)}》未提取到任何可用文本，已跳过。")
                    continue

                # OCR 结果提示：识别成功的页与识别失败被跳过的页
                if ocr_report.get("ocr_pages"):
                    if ocr_report.get("ocr_mode") == "force":
                        st.info(
                            f"《{escape_markdown(f.name)}》已强制全文 OCR，"
                            f"共 {len(ocr_report['ocr_pages'])} 页通过识别入库。"
                        )
                    else:
                        st.info(
                            f"《{escape_markdown(f.name)}》有 {len(ocr_report['ocr_pages'])} 个图片页已通过 OCR 识别入库。"
                        )
                if ocr_report.get("ocr_failed"):
                    pages = "、".join(str(p) for p in ocr_report["ocr_failed"])
                    st.warning(f"第 {pages} 页为图片且识别失败，已跳过。")
                if ocr_report.get("ocr_error") and not ocr_report.get("ocr_pages"):
                    logger.warning("《%s》OCR 不可用：%s", f.name, ocr_report["ocr_error"])
                    st.warning("OCR 未启用（图片识别组件不可用），图片页内容可能被跳过。")

                # 解析完成：进入向量化与写库阶段（真实耗时点，进度先给到 95%）
                progress_bar.progress(0.95, text="正在切分并写入数据库…")
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
                        st.info(f"《{escape_markdown(f.name)}》已入库，跳过。如需重新处理，请先删除它。")
                        continue
                    # 只有真正新增文档才计入「今日新传次数」。
                    quota.record_upload(USER_ID)
                    st.success(f"《{escape_markdown(f.name)}》入库完成：{n} 个片段（已持久化，重启不丢失）")
                except Exception:
                    logger.exception("《%s》入库失败", f.name)
                    st.error(f"《{escape_markdown(f.name)}》入库失败，请稍后重试。")
                    continue

                # v3.3：会话还没创建（首次提问才会创建）时先记下，创建后立即绑定
                if not st.session_state.session_id:
                    doc = db.get_document_by_name(USER_ID, f.name)
                    if doc:
                        st.session_state.pending_doc_ids.append(doc["id"])

                # 入库后自动生成全文摘要 + 3 个推荐问题（只在该文档内检索），
                # 生成结果持久化到文档上，随会话/文档恢复
                if not api_key:
                    st.info(
                        f"《{escape_markdown(f.name)}》已入库；"
                        "平台 Key 未配置，本次未生成摘要与推荐问题。"
                    )
                    continue
                # 摘要前查当天次数（免费版 5 次，防删传来回刷）；超限只跳过摘要。
                snap = quota.snapshot(USER_ID, TIER, ip_hash=IP_HASH)
                allowed, quota_msg = quota.check_summary(snap)
                if not allowed:
                    st.warning(quota_msg)
                    continue
                progress_bar.progress(0.99, text="正在生成摘要与推荐问题…")
                try:
                    ctx = similarity_search(
                        USER_ID,
                        "本文的主要内容、核心观点、结论与关键信息是什么",
                        top_k=6,
                        source=f.name,
                    )
                    usage = {}
                    quota.record_summary(USER_ID)
                    brief = generate_doc_brief(f.name, ctx, usage_out=usage)
                    if usage.get("total_tokens"):
                        brief["usage"] = usage  # 随摘要一起持久化，之后仍可看到
                        st.caption("📊 摘要生成 · " + format_usage(usage))
                    st.session_state.doc_briefs[f.name] = brief
                    save_document_summary(USER_ID, f.name, brief)  # 持久化
                except QAFriendlyError as e:
                    st.warning(f"《{escape_markdown(f.name)}》摘要生成失败：{e}")
                except Exception:
                    logger.exception("《%s》摘要生成失败", f.name)
                    st.warning(f"《{escape_markdown(f.name)}》已入库，但摘要生成失败，可稍后重试。")

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
        with st.container(border=True, key=f"doc_card_{d['id']}"):
            cols = st.columns([0.72, 0.28], vertical_alignment="center")
            cols[0].write(
                f"{icon} {in_session}{escape_markdown(name)} · {d.get('chunk_count', 0)} 片段"
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
                    f"🔍 《{escape_markdown(name)}》片段预览（前 {len(preview)} / {total} 段）",
                    expanded=False,
                ):
                    st.caption(
                        "以下为入库后的实际片段文本；扫描件展示的是 OCR 后的结果。"
                    )
                    for i, chunk in enumerate(preview, start=1):
                        st.markdown(f"**片段 {i} · 第 {chunk['page']} 页**")
                        st.text(chunk["text"])

    # DEF-02：一次性修复已入库旧片段（P2 的断字修复只对新解析文档生效）。
    # 只处理当前账号自己的片段，只改文本，不重新解析/OCR，可重复执行。
    st.divider()
    with st.expander("🔧 数据维护", expanded=False):
        st.caption(
            "对已入库的旧片段重新跑一次文本后处理：只修断字"
            "（如 suppor t@ → support@）和 OCR 字符混淆（如 ×1 → X1、-10C → -10℃），"
            "不会重新解析或重新 OCR；只影响当前账号，可重复执行。"
        )
        if st.button("修复历史片段断字", key="repair_chunks_btn"):
            with st.spinner("正在检查并修复已入库片段…"):
                stats = db.repair_chunk_texts(USER_ID, repair_chunk_text)
            if stats["repaired"]:
                st.success(
                    f"已修复 {stats['repaired']} 条片段"
                    f"（共检查 {stats['checked']} 条）。"
                )
            else:
                st.info(f"检查了 {stats['checked']} 条片段，没有需要修复的内容。")

    if st.session_state.get("pending_delete_doc"):
        _confirm_delete_document()


# ---------- 页面三：⚙️ 设置 ----------
def page_settings():
    """设置页：账号信息、修改用户名、设置或修改密码。"""
    st.markdown("## ⚙️ 设置")

    user = db.get_user_by_id(USER_ID) or {}
    created = user.get("created_at")
    created_str = created.strftime("%Y-%m-%d %H:%M") if created else "-"
    notice = st.session_state.pop("_settings_notice", None)
    if notice:
        st.success(notice)

    with st.container(border=True, key="settings_account"):
        st.markdown("**账号信息**")
        info_cols = st.columns(2)
        info_cols[0].text_input(
            "当前用户名",
            value=st.session_state.user["username"],
            disabled=True,
        )
        info_cols[1].text_input("注册时间", value=created_str, disabled=True)
        st.text_input(
            "邮箱",
            value=user.get("email") or "未绑定（历史账号）",
            disabled=True,
        )
        with st.form("username_form"):
            new_username = st.text_input(
                "新用户名",
                value=st.session_state.user["username"],
                help="3–20 位字母、数字或下划线；不能与其他账号重复。",
                key="settings_new_username",
            )
            rename = st.form_submit_button("更新用户名")
        if rename:
            ok, msg = auth.change_username(USER_ID, new_username)
            if ok:
                updated = db.get_user_by_id(USER_ID) or user
                st.session_state.user = {
                    "id": updated["id"],
                    "username": updated["username"],
                }
                st.session_state["_settings_notice"] = msg
                st.rerun()
            else:
                st.error(msg)
        st.caption(
            f"当前套餐：{quota.TIER_LABELS[TIER]}。付费版 9.9/月，"
            "开通请通过爱发电或微信转账联系管理员，确认收款后手动开通，页面不接支付。"
        )

    with st.container(border=True, key="settings_password"):
        if user.get("has_password"):
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
                        st.session_state["auth_notice"] = msg
                        st.session_state.user = None
                        _reset_chat_state()
                        st.rerun()
                    else:
                        st.error(msg)
        else:
            st.markdown("**设置密码**")
            st.caption(
                "当前账号没有密码，只能使用邮箱验证码登录。"
                "设置密码后即可使用用户名或邮箱 + 密码登录。"
            )
            with st.form("set_password_form"):
                new_password = st.text_input("新密码", type="password", help="至少 8 位")
                confirm_password = st.text_input("确认新密码", type="password")
                changed = st.form_submit_button("设置密码")
            if changed:
                if new_password != confirm_password:
                    st.error("两次输入的新密码不一致。")
                else:
                    ok, msg = auth.set_password(USER_ID, new_password)
                    if ok:
                        st.session_state["_settings_notice"] = msg
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
# 页面里可能刚用掉额度（提问、上传），结束时再渲染一次，确保顶栏数字准确。
_refresh_quota_topbar()
