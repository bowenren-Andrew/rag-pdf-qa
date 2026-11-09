# -*- coding: utf-8 -*-
"""用户等级、每日用量与额度拦截的单元测试（不涉及 Streamlit）。"""
from datetime import datetime

import db
import quota

D1 = "2026-11-09"
D2 = "2026-11-10"


def _user(name: str) -> dict:
    return db.create_user(name, "pbkdf2_sha256$1$00$00")


def test_新用户默认免费版(tmp_db):
    user = _user("quota_default")
    assert user["tier"] == "free"
    assert db.get_user_by_id(user["id"])["tier"] == "free"


def test_管理员可手动把用户改成付费版(tmp_db):
    user = _user("quota_paid")
    assert db.set_user_tier(user["id"], "paid") is True
    assert db.get_user_by_id(user["id"])["tier"] == "paid"
    assert db.set_user_tier_by_username("quota_paid", "free") is True
    assert db.get_user_by_id(user["id"])["tier"] == "free"


def test_tier只接受free或paid(tmp_db):
    user = _user("quota_bad_tier")
    try:
        db.set_user_tier(user["id"], "vip")
    except ValueError as e:
        assert "free" in str(e) and "paid" in str(e)
    else:  # pragma: no cover
        raise AssertionError("非法等级应报错")


def test_同一天多次操作只保留一行并累计(tmp_db):
    user = _user("quota_one_row")
    for _ in range(3):
        db.increment_daily_usage(user["id"], D1, "question")
    usage = db.get_daily_usage(user["id"], D1)
    assert usage["question_count"] == 3

    with db.session_scope() as s:
        rows = (
            s.query(db.DailyUsage)
            .filter(
                db.DailyUsage.user_id == user["id"],
                db.DailyUsage.usage_date == D1,
            )
            .count()
        )
    assert rows == 1


def test_不同日期用量互相独立(tmp_db):
    user = _user("quota_days")
    db.increment_daily_usage(user["id"], D1, "question")
    db.increment_daily_usage(user["id"], D2, "upload")

    assert db.get_daily_usage(user["id"], D1)["question_count"] == 1
    assert db.get_daily_usage(user["id"], D1)["upload_count"] == 0
    assert db.get_daily_usage(user["id"], D2)["question_count"] == 0
    assert db.get_daily_usage(user["id"], D2)["upload_count"] == 1


def test_免费版每日5问与当前剩余(tmp_db):
    user = _user("quota_free_limits")
    snap = quota.snapshot(user["id"], "free", on_date=D1)
    assert snap["remaining_questions"] == 5
    assert snap["remaining_documents"] == 3
    assert snap["remaining_uploads"] == 1
    assert snap["remaining_summaries"] == 5

    for _ in range(5):
        quota.record_question(user["id"], on_date=D1)
    snap = quota.snapshot(user["id"], "free", on_date=D1)
    assert snap["remaining_questions"] == 0
    assert quota.topbar_text(snap) == "今日已用完，明天再来"

    ok, msg = quota.check_question(snap)
    assert ok is False
    assert "今日提问次数已用完" in msg
    assert "每天 5 次" in msg
    assert "明天 0 点" in msg
    assert "9.9/月" in msg


def test_付费版每日50问_存50个文档_传5个(tmp_db):
    user = _user("quota_paid_limits")
    db.set_user_tier(user["id"], "paid")
    snap = quota.snapshot(user["id"], "paid", on_date=D1)
    assert snap["remaining_questions"] == 50
    assert snap["remaining_documents"] == 50
    assert snap["remaining_uploads"] == 5
    assert snap["remaining_summaries"] == 20

    for _ in range(50):
        quota.record_question(user["id"], on_date=D1)
    ok, msg = quota.check_question(quota.snapshot(user["id"], "paid", on_date=D1))
    assert ok is False
    assert "付费版" in msg
    assert "每天 50 次" in msg


def test_文档总数按当前存着的文档计算_删除后腾位置(tmp_db):
    user = _user("quota_docs")
    ids = [db.create_document(user["id"], f"d{i}.pdf") for i in range(3)]

    snap = quota.snapshot(user["id"], "free", on_date=D1)
    assert snap["document_count"] == 3
    assert snap["remaining_documents"] == 0
    ok, msg = quota.check_upload(snap)
    assert ok is False
    assert "文档存储已满" in msg
    assert "最多保存 3 个" in msg

    db.delete_document_record(user["id"], ids[0])
    snap = quota.snapshot(user["id"], "free", on_date=D1)
    assert snap["remaining_documents"] == 1
    assert quota.check_upload(snap)[0] is True


def test_免费版每天只能新传1个_第二天恢复(tmp_db):
    user = _user("quota_upload")
    quota.record_upload(user["id"], on_date=D1)

    snap = quota.snapshot(user["id"], "free", on_date=D1)
    ok, msg = quota.check_upload(snap)
    assert ok is False
    assert "今日新传文档次数已用完" in msg
    assert "最多 1 个" in msg
    assert "明天 0 点" in msg

    assert quota.check_upload(quota.snapshot(user["id"], "free", on_date=D2))[0] is True


def test_免费版摘要每天5次_防止删传来回刷(tmp_db):
    user = _user("quota_summary")
    for _ in range(5):
        quota.record_summary(user["id"], on_date=D1)

    snap = quota.snapshot(user["id"], "free", on_date=D1)
    ok, msg = quota.check_summary(snap)
    assert ok is False
    assert "今日摘要生成次数已用完" in msg
    assert "每天最多 5 次" in msg


def test_付费版摘要每天20次_第21次被拦(tmp_db):
    user = _user("quota_paid_summary")
    db.set_user_tier(user["id"], "paid")
    for _ in range(19):
        quota.record_summary(user["id"], on_date=D1)

    assert quota.check_summary(quota.snapshot(user["id"], "paid", on_date=D1))[0] is True

    quota.record_summary(user["id"], on_date=D1)
    snap = quota.snapshot(user["id"], "paid", on_date=D1)
    assert snap["remaining_summaries"] == 0
    ok, msg = quota.check_summary(snap)
    assert ok is False
    assert "今日摘要生成次数已用完" in msg
    assert "付费版每天最多 20 次" in msg
    assert "5 次" not in msg
    assert quota.check_summary(quota.snapshot(user["id"], "paid", on_date=D2))[0] is True


def test_北京时间0点跨日用量清零():
    before = datetime(2026, 11, 9, 23, 59, tzinfo=quota.BEIJING_TZ)
    after = datetime(2026, 11, 10, 0, 1, tzinfo=quota.BEIJING_TZ)
    assert quota.today_str(before) == "2026-11-09"
    assert quota.today_str(after) == "2026-11-10"


def test_顶部额度文案显示真实剩余(tmp_db):
    user = _user("quota_topbar")
    quota.record_question(user["id"], on_date=D1)
    snap = quota.snapshot(user["id"], "free", on_date=D1)
    assert quota.topbar_text(snap) == "今日还剩 4 问 / 还可存 3 个文档"
    assert "免费版" in quota.topbar_hint(snap)
    assert "9.9/月" in quota.topbar_hint(snap)
