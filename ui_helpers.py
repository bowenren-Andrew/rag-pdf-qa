# -*- coding: utf-8 -*-
"""
ui_helpers.py（v3.4）
职责：P3 体验层的小工具（纯函数 / 生成器），不依赖 Streamlit 运行时，便于单元测试。
- parse_progress_fraction / parse_progress_label：把解析回调的
  (stage, done, total) 映射成进度值与阶段文案（按真实页数推进，不做假动效）；
- reveal_on_first_chunk：包装流式生成器，首个 chunk 到达时回调、结束时必定回调，
  用于「思考中」占位在首字出现后消失、异常时也不残留。
- escape_markdown（P3-1 安全加固）：转义用户提供的文件名里的 Markdown 控制字符，
  避免被渲染成链接/图片/HTML，纯文本显示不变。
"""
import re


# P3-1：文件名会进入 st.write / st.markdown / st.info 等 Markdown 渲染点。
# 转义这些行内控制字符后，[CLICK](url)、![IMG](url)、<script> 都只显示为纯文本。
_MD_ESCAPE_RE = re.compile(r"([\\`*_\[\]()!<>~#|])")


def escape_markdown(text) -> str:
    """转义 Markdown 行内特殊字符（渲染后显示内容不变），用于文件名等用户输入。"""
    return _MD_ESCAPE_RE.sub(lambda m: "\\" + m.group(1), str(text or ""))


# 解析阶段在总进度里的区间：抽取 5%–65%，OCR 70%–90%，留给入库/摘要 95%+
PARSE_BASE = 0.05
PARSE_EXTRACT_SPAN = 0.60
PARSE_OCR_START = 0.70
PARSE_OCR_SPAN = 0.20
PARSE_MAX_BEFORE_WRITE = 0.92


def parse_progress_fraction(stage: str, done, total) -> float:
    """把解析回调的 (stage, done, total) 映射为 0–0.92 的进度值；异常输入返回起始值。"""
    try:
        total = int(total or 0)
        done = int(done or 0)
    except (TypeError, ValueError):
        return PARSE_BASE
    if total <= 0:
        return PARSE_BASE
    ratio = min(max(done / total, 0.0), 1.0)
    if stage == "ocr":
        value = PARSE_OCR_START + PARSE_OCR_SPAN * ratio
    else:
        value = PARSE_BASE + PARSE_EXTRACT_SPAN * ratio
    return round(min(value, PARSE_MAX_BEFORE_WRITE), 4)


def parse_progress_label(stage: str, done, total, unit: str = "页") -> str:
    """阶段文案：显示真实页数进度，让用户知道解析走到哪一步。"""
    try:
        done = int(done or 0)
        total = int(total or 0)
    except (TypeError, ValueError):
        done, total = 0, 0
    if stage == "ocr":
        return f"正在 OCR 识别…（{done}/{total} {unit}）"
    return f"正在抽取文本…（{done}/{total} {unit}）"


def reveal_on_first_chunk(stream, on_first=None, on_finish=None):
    """
    包装流式生成器：
    - 第一个 chunk 到来时调用 on_first（只调一次），用于清掉「思考中」占位；
    - 无论正常结束、空流还是中途抛错，最后都调用 on_finish，保证不残留占位。
    """
    first = True
    try:
        for piece in stream:
            if first:
                first = False
                if on_first is not None:
                    on_first()
            yield piece
    finally:
        if on_finish is not None:
            on_finish()


ASK_BANNER_MAX_CHARS = 24


def ask_banner_text(question: str) -> str:
    """
    页面顶部「思考中」提示的文案。
    点击发送后 Streamlit 要重跑整个脚本才会渲染到聊天区（线上约 3–4 秒），
    这段时间先由顶部提示承担反馈；问题过长时截断，避免撑开布局。
    """
    text = " ".join((question or "").split())
    if len(text) > ASK_BANNER_MAX_CHARS:
        text = text[:ASK_BANNER_MAX_CHARS] + "…"
    return f"⏳ **思考中…** 正在准备回答：{text}"


def mark_ask_pending(state, question) -> None:
    """
    把「刚点击发送」的问题记到会话状态里，供下一次重跑在页面顶部先给反馈。
    只写入非空问题；空问题（按钮本应禁用）不记录，避免留下无意义的提示。
    """
    text = (question or "").strip()
    if text:
        state["ask_pending"] = text
