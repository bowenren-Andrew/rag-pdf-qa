# -*- coding: utf-8 -*-
"""
qa.py（v2）
职责：调用 DeepSeek API 生成回答（单一职责，不涉及界面与向量库）。
- API Key 从项目根目录的 .env 文件读取（python-dotenv）；
  系统环境变量优先级更高（load_dotenv 默认不覆盖已有变量）。
- 提供三种能力：
  1) generate_answer     —— 非流式回答（备用一次性调用）
  2) stream_answer       —— 流式回答（逐段 yield，供 st.write_stream 使用）
  3) generate_doc_brief  —— 文档摘要 + 3 个推荐问题
- 所有失败都抛出 QAFriendlyError（中文友好提示），由界面层展示，程序不崩溃。
"""
import json
import os
import re
from typing import Iterator

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
)

# DeepSeek 官方接口（OpenAI 兼容）
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"

# 项目根目录 = 本文件所在目录，.env 与 app.py 同级
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

# 加载 .env；override=False 表示已存在的系统环境变量不会被覆盖（系统变量优先）
load_dotenv(ENV_PATH, override=False)


class QAFriendlyError(Exception):
    """面向用户的中文友好错误，界面直接 str(e) 展示即可。"""


def get_api_key() -> str:
    """读取 API Key（来自系统环境变量或 .env 文件），未配置时返回空字符串。"""
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def _require_client() -> OpenAI:
    """校验 API Key 并创建客户端；Key 缺失时给出中文提示。"""
    api_key = get_api_key()
    if not api_key:
        raise QAFriendlyError(
            "未检测到 API Key。请在项目根目录的 .env 文件中配置 "
            "DEEPSEEK_API_KEY=你的Key（可参考 .env.example），"
            "或设置系统环境变量后重启应用。"
        )
    return OpenAI(api_key=api_key, base_url=BASE_URL, timeout=60)


def _build_prompt(question: str, contexts: list[dict]) -> str:
    """把检索到的片段拼进提示词，要求模型仅依据资料回答并标注引用编号。"""
    parts = []
    for i, c in enumerate(contexts, start=1):
        parts.append(f"【资料{i}】（来源：{c['source']}，第{c['page']}页）\n{c['text']}")
    refs = "\n\n".join(parts)
    return (
        "你是一个严谨的 PDF 问答助手。请只根据下面的参考资料回答用户问题，"
        "并在回答中标注引用的资料编号（如【资料1】）。"
        "如果参考资料无法回答问题，请明确说明“根据现有资料无法回答”，不要编造内容。\n\n"
        f"参考资料：\n{refs}\n\n用户问题：{question}\n\n回答："
    )


def _friendly(e: Exception) -> QAFriendlyError:
    """把 SDK 异常统一转换成中文友好错误。"""
    if isinstance(e, AuthenticationError):
        return QAFriendlyError("DeepSeek API Key 无效或已过期，请检查 .env 中的 DEEPSEEK_API_KEY。")
    if isinstance(e, APITimeoutError):
        return QAFriendlyError("调用 DeepSeek API 超时，请检查网络后重试。")
    if isinstance(e, APIConnectionError):
        return QAFriendlyError("无法连接 DeepSeek API，请检查网络或代理设置。")
    if isinstance(e, APIStatusError):
        return QAFriendlyError(f"DeepSeek API 返回错误（HTTP {e.status_code}）：{e.message}")
    return QAFriendlyError(f"调用 DeepSeek API 失败：{e}")


def generate_answer(question: str, contexts: list[dict]) -> str:
    """非流式生成回答（一次性返回完整文本，备用）。"""
    client = _require_client()
    if not contexts:
        raise QAFriendlyError("没有检索到相关内容，请先上传并成功解析 PDF 文件。")
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是一个严谨的 PDF 问答助手，回答使用简体中文。"},
                {"role": "user", "content": _build_prompt(question, contexts)},
            ],
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        raise _friendly(e)


def stream_answer(question: str, contexts: list[dict]) -> Iterator[str]:
    """流式生成回答：逐段 yield 文本，配合 st.write_stream 实现打字机效果。"""
    client = _require_client()
    if not contexts:
        raise QAFriendlyError("没有检索到相关内容，请先上传并成功解析 PDF 文件。")
    try:
        stream = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是一个严谨的 PDF 问答助手，回答使用简体中文。"},
                {"role": "user", "content": _build_prompt(question, contexts)},
            ],
            temperature=0.3,
            stream=True,
        )
        for chunk in stream:
            # 流式结束时的 usage 块 choices 可能为空列表，需先保护
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except Exception as e:
        raise _friendly(e)


def generate_doc_brief(filename: str, contexts: list[dict]) -> dict:
    """
    文档上传后自动生成：全文摘要 + 3 个推荐问题。
    :return: {"summary": 摘要文本, "questions": [问题1, 问题2, 问题3]}
    """
    client = _require_client()
    refs = "\n\n".join(
        f"【片段{i}】（第{c['page']}页）\n{c['text']}" for i, c in enumerate(contexts, start=1)
    )
    prompt = (
        f"下面是《{filename}》的部分内容片段。请输出严格合法的 JSON，不要包含 markdown 代码块：\n"
        '{"summary": "200 字以内的中文全文摘要", '
        '"questions": ["问题1", "问题2", "问题3"]}\n'
        "要求：3 个问题是读者最可能想问、且能由该文档回答的具体问题，每条不超过 30 字。\n\n"
        f"内容片段：\n{refs}"
    )
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是文档分析助手，输出严格 JSON，使用简体中文。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        raw = resp.choices[0].message.content.strip()
    except Exception as e:
        raise _friendly(e)

    # 兼容模型把 JSON 包在 ```json 代码块里的情况
    raw = re.sub(r"^```(?:json)?|```$", "", raw).strip()
    try:
        data = json.loads(raw)
        questions = [str(q).strip() for q in data.get("questions", [])][:3]
        return {"summary": str(data.get("summary", "")).strip(), "questions": questions}
    except Exception:
        # 解析失败时降级：把原文当摘要，推荐问题留空，不让流程中断
        return {"summary": raw, "questions": []}
