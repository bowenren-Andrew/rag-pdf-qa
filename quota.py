# -*- coding: utf-8 -*-
"""
quota.py（v3.8）
职责：用户等级、每日用量与额度判断（单一职责，不直接依赖 Streamlit）。

定稿规则（2026-11-09）：
- free：每天 5 次提问；总共最多存 3 个文档；每天最多新传 1 个文档；
        每天最多生成 5 次摘要（防止删了传、传了删来回刷）。
- paid（9.9/月）：每天 50 次提问；总共最多存 50 个文档；
        每天最多新传 5 个文档；摘要随文档上传生成，不再宣传「不限」。
- 每天 0 点清零：日期按北京时间取 YYYY-MM-DD，只查当天行，不需要定时任务。
"""
from datetime import datetime, timedelta, timezone

import db

BEIJING_TZ = timezone(timedelta(hours=8))

TIER_FREE = "free"
TIER_PAID = "paid"
TIER_LABELS = {TIER_FREE: "免费版", TIER_PAID: "付费版"}

# 摘要与每次上传文档绑定：免费版允许 5 次重试；付费版每天最多新传 5 个文档，
# 因此摘要日上限同样取 5，不对外写「不限」。
TIER_LIMITS = {
    TIER_FREE: {
        "questions": 5,
        "documents": 3,
        "uploads": 1,
        "summaries": 5,
    },
    TIER_PAID: {
        "questions": 50,
        "documents": 50,
        "uploads": 5,
        "summaries": 5,
    },
}

PAID_PRICE_TEXT = "9.9/月"


def normalize_tier(tier: str) -> str:
    """未知等级一律按免费版处理，避免脏数据绕过或打崩界面。"""
    return TIER_PAID if str(tier or "").strip().lower() == TIER_PAID else TIER_FREE


def today_str(now: datetime = None) -> str:
    """当前北京时间的日期字符串（YYYY-MM-DD）；测试可传入固定时间。"""
    current = now or datetime.now(BEIJING_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BEIJING_TZ)
    else:
        current = current.astimezone(BEIJING_TZ)
    return current.date().isoformat()


def snapshot(user_id: int, tier: str, on_date: str = None) -> dict:
    """
    读取当前额度快照：等级、限制、今日已用、当前文档总数与各项剩余。
    界面和调用前拦截都用这一份数据，避免两处规则不一致。
    """
    tier = normalize_tier(tier)
    usage_date = on_date or today_str()
    limits = dict(TIER_LIMITS[tier])
    usage = db.get_daily_usage(user_id, usage_date)
    document_count = db.count_user_documents(user_id)
    return {
        "tier": tier,
        "tier_label": TIER_LABELS[tier],
        "date": usage_date,
        "limits": limits,
        "used": usage,
        "document_count": document_count,
        "remaining_questions": max(
            limits["questions"] - int(usage["question_count"] or 0), 0
        ),
        "remaining_uploads": max(
            limits["uploads"] - int(usage["upload_count"] or 0), 0
        ),
        "remaining_summaries": max(
            limits["summaries"] - int(usage["summary_count"] or 0), 0
        ),
        "remaining_documents": max(limits["documents"] - document_count, 0),
    }


def topbar_text(snap: dict) -> str:
    """顶部「今日剩余额度」文案：真实剩余；提问用完时给出明确结束语。"""
    if snap["remaining_questions"] <= 0:
        return "今日已用完，明天再来"
    return (
        f"今日还剩 {snap['remaining_questions']} 问 / "
        f"还可存 {snap['remaining_documents']} 个文档"
    )


def topbar_hint(snap: dict) -> str:
    """顶部额度悬浮说明：写清等级与付费版对应数字。"""
    tier = snap["tier"]
    limits = snap["limits"]
    if tier == TIER_PAID:
        return (
            f"付费版（{PAID_PRICE_TEXT}）：每天 {limits['questions']} 次提问，"
            f"最多存 {limits['documents']} 个文档"
        )
    paid = TIER_LIMITS[TIER_PAID]
    return (
        f"免费版：每天 {limits['questions']} 次提问，最多存 {limits['documents']} 个文档；"
        f"付费版（{PAID_PRICE_TEXT}）：每天 {paid['questions']} 次提问，"
        f"最多存 {paid['documents']} 个文档"
    )


def _upgrade_hint(tier: str, kind: str) -> str:
    """免费版被拦时补一句付费版对应额度；付费版不再推销。"""
    if tier != TIER_FREE:
        return ""
    paid = TIER_LIMITS[TIER_PAID]
    if kind == "question":
        return f"付费版（{PAID_PRICE_TEXT}）每天 {paid['questions']} 次提问。"
    if kind == "upload":
        return f"付费版（{PAID_PRICE_TEXT}）每天最多新传 {paid['uploads']} 个文档。"
    if kind == "documents":
        return f"付费版（{PAID_PRICE_TEXT}）最多保存 {paid['documents']} 个文档。"
    if kind == "summary":
        return (
            f"付费版（{PAID_PRICE_TEXT}）摘要随文档上传生成，"
            f"每天最多 {paid['summaries']} 次。"
        )
    return ""


def _over_limit_message(snap: dict, kind: str) -> str:
    """按被拦项目生成中文提示：说清哪项满了、何时恢复、付费版多少。"""
    tier = snap["tier"]
    label = snap["tier_label"]
    limits = snap["limits"]
    recovery = "明天 0 点（北京时间）恢复。"
    if kind == "question":
        return (
            f"今日提问次数已用完（{label}每天 {limits['questions']} 次）。"
            + recovery
            + _upgrade_hint(tier, "question")
        )
    if kind == "upload":
        return (
            f"今日新传文档次数已用完（{label}每天最多 {limits['uploads']} 个）。"
            + recovery
            + _upgrade_hint(tier, "upload")
        )
    if kind == "summary":
        return (
            f"今日摘要生成次数已用完（{label}每天最多 {limits['summaries']} 次）。"
            + recovery
            + _upgrade_hint(tier, "summary")
        )
    if kind == "documents":
        return (
            f"文档存储已满（{label}最多保存 {limits['documents']} 个，"
            f"当前已存 {snap['document_count']} 个）。删除不需要的文档后可继续上传。"
            + _upgrade_hint(tier, "documents")
        )
    return "额度不足，请稍后重试。"


def check_question(snap: dict) -> tuple[bool, str]:
    """提问前检查：返回 (是否可继续, 中文提示)；被拦时不调 API、不记用量。"""
    if snap["remaining_questions"] <= 0:
        return False, _over_limit_message(snap, "question")
    return True, ""


def check_upload(snap: dict) -> tuple[bool, str]:
    """上传前检查：先看总存储上限，再看当天新传次数；都不扣费、不解析。"""
    if snap["remaining_documents"] <= 0:
        return False, _over_limit_message(snap, "documents")
    if snap["remaining_uploads"] <= 0:
        return False, _over_limit_message(snap, "upload")
    return True, ""


def check_summary(snap: dict) -> tuple[bool, str]:
    """生成摘要前检查；被拦时文档仍可入库，只是不再调用摘要接口。"""
    if snap["remaining_summaries"] <= 0:
        return False, _over_limit_message(snap, "summary")
    return True, ""


def record_question(user_id: int, on_date: str = None) -> dict:
    """提问实际发起后记 1 次；被拦截的请求不会走到这里。"""
    return db.increment_daily_usage(user_id, on_date or today_str(), "question")


def record_upload(user_id: int, on_date: str = None) -> dict:
    """文档成功入库后记 1 次；解析失败、查重跳过不会走到这里。"""
    return db.increment_daily_usage(user_id, on_date or today_str(), "upload")


def record_summary(user_id: int, on_date: str = None) -> dict:
    """摘要实际发起后记 1 次，防止删传来回刷摘要。"""
    return db.increment_daily_usage(user_id, on_date or today_str(), "summary")
