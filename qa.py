# -*- coding: utf-8 -*-
"""
qa.py（v3.1）
职责：调用 DeepSeek API 生成回答（单一职责，不涉及界面与向量库）。
- v3 变更：API Key **不再读全局环境变量**，改为由调用方传入（每位用户使用自己在
  「设置」页填写、经 Fernet 加密保存后解密出来的 Key）；
  仅保留 get_api_key() 作为本地开发/单元测试的兼容回退。
- v3.1 变更：stream_answer / generate_answer 增加可选参数 history，支持多轮对话；
  提示词改为「系统提示 → 对话历史 → 参考资料 → 当前问题」四段，
  并明确历史只作背景参考、回答仍只依据本次参考资料。
- 提供三种能力：
  1) generate_answer     —— 非流式回答（备用一次性调用）
  2) stream_answer       —— 流式回答（逐段 yield，供 st.write_stream 使用）
  3) generate_doc_brief  —— 文档摘要 + 3 个推荐问题（不传历史）
- 所有失败都抛出 QAFriendlyError（中文友好提示），由界面层展示，程序不崩溃。
- v3.4 变更（P3）：三个调用函数新增可选参数 usage_out，把 API 返回的 token 用量
  （含缓存命中/未命中）回传给界面；新增 estimate_cost / format_usage，
  按 DeepSeek 官方价目估算本轮费用；流式请求显式开启 include_usage。
"""
import datetime as dt
import json
import logging
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
# 请求使用的模型名。deepseek-chat 是历史别名，实际服务的模型以响应中的 model 字段为准，
# 界面展示的也是响应返回值（不把猜测写死）。
MODEL = "deepseek-chat"

# ---------------- P3：用量与费用估算 ----------------
# 官方价目（人民币 / 百万 tokens），来源：DeepSeek 开放平台「模型 & 价格」页（2026-11 查询）。
# 高峰时段 = 北京时间周一至周五 9:00–12:00、14:00–18:00；其余时段为空闲价。
# 法定节假日未单独扣除，节假日会按高峰价估算（偏保守）；价格调整时只需改这里。
PRICE_TABLE = {
    "flash": {
        "peak": {"cache_hit": 0.04, "cache_miss": 2.0, "output": 8.0},
        "offpeak": {"cache_hit": 0.02, "cache_miss": 1.0, "output": 4.0},
    },
    "pro": {
        "peak": {"cache_hit": 0.30, "cache_miss": 9.0, "output": 27.0},
        "offpeak": {"cache_hit": 0.15, "cache_miss": 4.5, "output": 13.5},
    },
}
BEIJING_TZ = dt.timezone(dt.timedelta(hours=8))

logger = logging.getLogger(__name__)


def _model_tier(model_name: str) -> str:
    """按响应里的模型名选择价目档位：带 pro 的走 Pro 价，其余按 Flash 价估算。"""
    return "pro" if "pro" in str(model_name or "").lower() else "flash"


def is_peak_time(now: dt.datetime = None) -> bool:
    """判断北京时间是否处于 DeepSeek 高峰时段（法定节假日未单独扣除）。"""
    current = now or dt.datetime.now(BEIJING_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BEIJING_TZ)
    else:
        current = current.astimezone(BEIJING_TZ)
    if current.weekday() >= 5:
        return False
    return 9 <= current.hour < 12 or 14 <= current.hour < 18


def _usage_field(usage, name):
    """读取 usage 字段，同时兼容 SDK 对象、dict 与 model_extra 里的扩展字段。"""
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage.get(name)
    value = getattr(usage, name, None)
    if value is None:
        extra = getattr(usage, "model_extra", None) or {}
        if isinstance(extra, dict):
            value = extra.get(name)
    return value


def usage_to_dict(usage, model: str = None) -> dict:
    """把 API 返回的 usage 统一成可持久化、可展示的 dict。"""
    prompt = int(_usage_field(usage, "prompt_tokens") or 0)
    completion = int(_usage_field(usage, "completion_tokens") or 0)
    total = int(_usage_field(usage, "total_tokens") or (prompt + completion))
    hit = _usage_field(usage, "prompt_cache_hit_tokens")
    if hit is None:
        hit = _usage_field(_usage_field(usage, "prompt_tokens_details"), "cached_tokens")
    miss = _usage_field(usage, "prompt_cache_miss_tokens")
    hit = None if hit is None else int(hit)
    miss = None if miss is None else int(miss)
    if hit is None and miss is not None:
        hit = max(prompt - miss, 0)
    elif miss is None and hit is not None:
        miss = max(prompt - hit, 0)
    return {
        "model": model or MODEL,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "prompt_cache_hit_tokens": hit,
        "prompt_cache_miss_tokens": miss,
    }


def _capture_usage(usage_out: dict, usage, model=None) -> None:
    """把一次调用的 usage 写进调用方传入的 dict；传 None 或解析失败时静默跳过。"""
    if usage_out is None or usage is None:
        return
    try:
        name = model if isinstance(model, str) and model else None
        usage_out.update(usage_to_dict(usage, name))
    except (TypeError, ValueError):
        logger.debug("无法解析 API usage：%r", usage)


def estimate_cost(usage: dict, now: dt.datetime = None) -> float:
    """按官方价目估算一次调用的费用（单位：元）。"""
    usage = usage or {}
    period = "peak" if is_peak_time(now) else "offpeak"
    prices = PRICE_TABLE[_model_tier(usage.get("model"))][period]
    prompt = int(usage.get("prompt_tokens") or 0)
    hit = usage.get("prompt_cache_hit_tokens")
    hit = 0 if hit is None else int(hit)
    miss = usage.get("prompt_cache_miss_tokens")
    miss = max(prompt - hit, 0) if miss is None else int(miss)
    completion = int(usage.get("completion_tokens") or 0)
    return (
        hit * prices["cache_hit"]
        + miss * prices["cache_miss"]
        + completion * prices["output"]
    ) / 1_000_000


def format_usage(usage: dict, now: dt.datetime = None) -> str:
    """生成给界面展示的一行用量说明；没有用量数据时返回空字符串。"""
    if not usage or not usage.get("total_tokens"):
        return ""
    cost = estimate_cost(usage, now=now)
    period = "高峰价" if is_peak_time(now) else "空闲价"
    prompt = int(usage.get("prompt_tokens") or 0)
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    miss = max(prompt - int(hit or 0), 0) if miss is None else int(miss)
    hit_part = f"（缓存命中 {int(hit):,}）" if hit is not None else ""
    completion = int(usage.get("completion_tokens") or 0)
    return (
        f"模型 {usage.get('model') or MODEL} · 输入 {prompt:,} tokens{hit_part}"
        f" · 缓存未命中 {miss:,} · 输出 {completion:,} tokens"
        f" · 估算费用 ¥{cost:.4f}（按官方{period}，仅供参考）"
    )


# 项目根目录 = 本文件所在目录，.env 与 app.py 同级
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

# 加载 .env；override=False 表示已存在的系统环境变量不会被覆盖（系统变量优先）
load_dotenv(ENV_PATH, override=False)


class QAFriendlyError(Exception):
    """面向用户的中文友好错误，界面直接 str(e) 展示即可。"""


def get_api_key() -> str:
    """
    读取全局兜底 Key（系统环境变量或 .env）。
    v3 说明：主路径已改为"每位用户用自己的 Key"，此函数仅作为本地开发与单元测试的兼容回退。
    """
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def _require_client(api_key: str = None) -> OpenAI:
    """
    校验 API Key 并创建客户端。
    :param api_key: 当前登录用户的明文 Key（由 auth 模块解密后传入）；
                    不传时回退读全局环境变量（本地兼容/测试用）。
    Key 缺失时抛出中文友好提示。
    """
    key = (api_key or "").strip() or get_api_key()
    if not key:
        raise QAFriendlyError(
            "未检测到 API Key。请到「⚙️ 设置」页填写你自己的 DeepSeek Key 后再提问。"
        )
    return OpenAI(api_key=key, base_url=BASE_URL, timeout=60)


def verify_api_key(api_key: str) -> None:
    """
    用 GET /models 做一次轻量鉴权：只验证 Key，不消耗 token、不生成内容。
    :raises QAFriendlyError: Key 无效、无权限、网络失败或接口异常（均为中文提示）
    """
    key = (api_key or "").strip()
    if not key:
        raise QAFriendlyError("请输入要保存的 API Key。")
    client = OpenAI(api_key=key, base_url=BASE_URL, timeout=10, max_retries=0)
    try:
        client.models.list()
    except AuthenticationError as e:
        raise QAFriendlyError(
            "Key 校验失败：该 Key 无效或已过期，请确认后重新填写。"
        ) from e
    except APITimeoutError as e:
        raise QAFriendlyError(
            "Key 校验超时，请检查网络后重试（Key 未保存）。"
        ) from e
    except APIConnectionError as e:
        raise QAFriendlyError(
            "无法连接 DeepSeek 校验 Key，请检查网络后重试（Key 未保存）。"
        ) from e
    except APIStatusError as e:
        if e.status_code in (401, 403):
            raise QAFriendlyError(
                "Key 校验失败：该 Key 无效或没有访问权限，请确认后重新填写。"
            ) from e
        raise QAFriendlyError(
            f"Key 校验失败：DeepSeek 接口返回 HTTP {e.status_code}，"
            "请稍后重试（Key 未保存）。"
        ) from e
    except Exception as e:
        logger.warning("校验 DeepSeek Key 失败：%s", e)
        raise QAFriendlyError(
            "Key 校验失败：无法完成验证，请稍后重试（Key 未保存）。"
        ) from e


# 多轮对话历史的字符上限：超出时从最旧的消息开始丢弃，最近一轮问答一定保留
HISTORY_MAX_CHARS = 3000


def truncate_history(history: list[dict], max_chars: int = HISTORY_MAX_CHARS) -> list[dict]:
    """
    按总字符数截断对话历史：从最旧的消息开始丢弃，但**最近一轮问答必定保留**。
    :param history: [{"role": "user"/"assistant", "content": 文本}, ...]
    :param max_chars: 总字符上限
    :return: 截断后的历史（原顺序不变；空输入返回空列表）
    """
    msgs = [
        m for m in (history or [])
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]
    if not msgs:
        return []

    def total(items: list[dict]) -> int:
        return sum(len(str(m.get("content", ""))) for m in items)

    if total(msgs) <= max_chars:
        return msgs

    # 最近一轮问答 = 最后两条消息（一问一答），无论如何都保留
    keep_tail = 2 if len(msgs) >= 2 else 1
    cut = 0
    # 从最旧往新丢，直到总长度达标；cut 最多推进到只剩最近一轮
    while cut + keep_tail < len(msgs) and total(msgs[cut:]) > max_chars:
        cut += 1
    return msgs[cut:]


def _build_prompt(question: str, contexts: list[dict], history: list[dict] = None) -> str:
    """
    拼装提示词，结构为四段：
    1) 系统提示（严谨问答助手 + 只依据本次资料回答）
    2) 对话历史（可选，按时间顺序「用户：… / 助手：…」）
    3) 本次检索到的参考资料（保持【资料N】格式与引用编号要求）
    4) 当前问题
    """
    system_part = (
        "你是一个严谨的文档问答助手。请只根据下面的参考资料回答用户问题，"
        "并在回答中标注引用的资料编号（如【资料1】）。"
        "历史对话只作背景参考，回答内容仍必须只依据本次检索到的参考资料；"
        "如果本次参考资料无法回答问题，请明确说明“根据现有资料无法回答”，"
        "不要用历史内容编造。"
    )
    history_part = ""
    if history:
        lines = [
            f"{'用户' if m.get('role') == 'user' else '助手'}：{m.get('content', '')}"
            for m in history
        ]
        history_part = "对话历史：\n" + "\n".join(lines) + "\n\n"

    parts = []
    for i, c in enumerate(contexts, start=1):
        parts.append(f"【资料{i}】（来源：{c['source']}，第{c['page']}页）\n{c['text']}")
    refs = "\n\n".join(parts)
    return (
        f"{system_part}\n\n"
        f"{history_part}"
        f"参考资料：\n{refs}\n\n用户问题：{question}\n\n回答："
    )


def _friendly(e: Exception) -> QAFriendlyError:
    """把 SDK 异常统一转换成中文友好错误。"""
    if isinstance(e, AuthenticationError):
        return QAFriendlyError(
            "你填写的 DeepSeek API Key 无效或已过期，请到「⚙️ 设置」页更新。"
        )
    if isinstance(e, APITimeoutError):
        return QAFriendlyError("调用 DeepSeek API 超时，请检查网络后重试。")
    if isinstance(e, APIConnectionError):
        return QAFriendlyError("无法连接 DeepSeek API，请检查网络或代理设置。")
    if isinstance(e, APIStatusError):
        return QAFriendlyError(f"DeepSeek API 返回错误（HTTP {e.status_code}）：{e.message}")
    return QAFriendlyError(f"调用 DeepSeek API 失败：{e}")


def generate_answer(
    question: str,
    contexts: list[dict],
    history: list[dict] = None,
    api_key: str = None,
    usage_out: dict = None,
) -> str:
    """
    非流式生成回答（一次性返回完整文本，备用）。
    :param history: 可选，多轮对话历史 [{"role","content"}, ...]，内部会先做截断保护
    :param usage_out: 可选，传入空 dict 时把 API 返回的用量写进去（供界面展示）
    """
    client = _require_client(api_key)
    if not contexts:
        raise QAFriendlyError("没有检索到相关内容，请先上传并成功解析文档。")
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是一个严谨的 PDF 问答助手，回答使用简体中文。"},
                {
                    "role": "user",
                    "content": _build_prompt(question, contexts, truncate_history(history)),
                },
            ],
            temperature=0.3,
        )
        _capture_usage(
            usage_out, getattr(resp, "usage", None), getattr(resp, "model", None)
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        raise _friendly(e)


def stream_answer(
    question: str,
    contexts: list[dict],
    history: list[dict] = None,
    api_key: str = None,
    usage_out: dict = None,
) -> Iterator[str]:
    """
    流式生成回答：逐段 yield 文本，配合 st.write_stream 实现打字机效果。
    :param history: 可选，多轮对话历史 [{"role","content"}, ...]，内部会先做截断保护
    :param usage_out: 可选，传入空 dict 时把 API 返回的用量写进去（供界面展示）
    """
    client = _require_client(api_key)
    if not contexts:
        raise QAFriendlyError("没有检索到相关内容，请先上传并成功解析文档。")
    try:
        stream = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是一个严谨的 PDF 问答助手，回答使用简体中文。"},
                {
                    "role": "user",
                    "content": _build_prompt(question, contexts, truncate_history(history)),
                },
            ],
            temperature=0.3,
            stream=True,
            stream_options={"include_usage": True},
        )
        for chunk in stream:
            _capture_usage(
                usage_out, getattr(chunk, "usage", None), getattr(chunk, "model", None)
            )
            # 流式结束时的 usage 块 choices 可能为空列表，需先保护
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except Exception as e:
        raise _friendly(e)


def generate_doc_brief(
    filename: str,
    contexts: list[dict],
    api_key: str = None,
    usage_out: dict = None,
) -> dict:
    """
    文档上传后自动生成：全文摘要 + 3 个推荐问题。
    :return: {"summary": 摘要文本, "questions": [问题1, 问题2, 问题3]}
    :param usage_out: 可选，传入空 dict 时把 API 返回的用量写进去（供界面展示）
    """
    client = _require_client(api_key)
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
        _capture_usage(
            usage_out, getattr(resp, "usage", None), getattr(resp, "model", None)
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
