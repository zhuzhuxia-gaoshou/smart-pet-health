# -*- coding: utf-8 -*-
"""后端核心单测：roll_date 钳制 / 花费聚合与校验 / 饮食小结 / 日历聚合 / complete 幂等 / 快照备份。
运行：backend/.venv/Scripts/python.exe -m pytest backend/tests -q
"""
import os
import sqlite3
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

import db
from main import ExpenseIn, _check_month


# ---------------------------------------------------------------- roll_date（月末钳制是有意的产品决策）

def test_roll_date_month_end_clamp():
    assert db.roll_date("2027-01-31", "monthly") == "2027-02-28"   # 1/31 → 2/28
    assert db.roll_date("2024-01-31", "monthly") == "2024-02-29"   # 闰年 2/29
    assert db.roll_date("2024-02-29", "yearly") == "2025-02-28"    # 年度钳制
    assert db.roll_date("2026-03-31", "monthly") == "2026-04-30"


def test_roll_date_simple_periods():
    assert db.roll_date("2026-09-17", "daily") == "2026-09-18"
    assert db.roll_date("2026-09-17", "weekly") == "2026-09-24"
    assert db.roll_date("2026-09-17", "yearly") == "2027-09-17"
    assert db.roll_date("2026-09-17", "") == "2026-09-17"          # 未知规则原样返回


# ---------------------------------------------------------------- 花费聚合与校验

def test_expense_summary_aggregates_and_filters(tmp_db):
    keke = db.fetch_pet_by_name("测测")
    db.add_expense({"pet_id": keke["id"], "category": "medical", "amount": 12.34, "date": "2026-09-01"})
    db.add_expense({"pet_id": None, "category": "food", "amount": 0.66, "date": "2026-09-02"})
    db.add_expense({"pet_id": keke["id"], "category": "food", "amount": 100.00, "date": "2026-08-30"})

    sep = db.expense_summary(2026, 9)
    assert sep["total"] == round(12.34 + 0.66, 2) and sep["count"] == 2
    assert sep["by_category"]["medical"] == 12.34 and sep["by_category"]["food"] == 0.66
    assert any(p["pet_id"] is None and p["pet_name"] == "家庭共同" for p in sep["by_pet"])

    only_medical = db.expense_summary(2026, 9, category="medical")
    assert only_medical["total"] == 12.34 and only_medical["count"] == 1

    aug = db.expense_summary(2026, 8)
    assert aug["total"] == 100.0 and aug["count"] == 1


def test_expense_rounding_and_null_pet_flow(tmp_db):
    keke = db.fetch_pet_by_name("两两")
    e = db.add_expense({"pet_id": keke["id"], "category": "other", "amount": 9.999, "date": "2026-09-10"})
    assert e["amount"] == 10.0                      # 入库前 round 两位
    upd = db.update_expense(e["id"], {"pet_id": 0, "amount": 5})   # 0 = 转家庭共同
    assert upd["pet_id"] is None
    assert db.delete_expense(e["id"]) is True
    assert db.get_expense(e["id"]) is None


def test_expense_validation_chinese_422():
    with pytest.raises(ValidationError) as ei:
        ExpenseIn(category="food", amount=-1)
    assert "金额" in str(ei.value.errors()[0]["msg"])
    with pytest.raises(ValidationError):
        ExpenseIn(category="food", amount=1.005)     # 三位小数被整数化比较拦截
    with pytest.raises(ValidationError):
        ExpenseIn(category="toys", amount=5)         # 分类白名单
    assert _check_month(2026, 13) and "月份" in _check_month(2026, 13)
    assert _check_month(2026, 9) is None


# ---------------------------------------------------------------- 饮食小结

def test_feeding_summary_today_week_and_types(tmp_db):
    keke = db.fetch_pet_by_name("测测")
    today = date.today().isoformat()
    monday = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    db.add_feeding_log(keke["id"], {"food_type": "kibble", "amount": "70 g", "date": today})
    db.add_feeding_log(keke["id"], {"food_type": "wet", "amount": "1 罐", "date": monday})
    s = db.feeding_summary(keke["id"])
    assert s["today"] >= 1 and s["week"] >= 2
    assert s["by_type_30d"]["kibble"] >= 1 and s["by_type_30d"]["wet"] >= 1
    bad = db.add_feeding_log(keke["id"], {"food_type": "pizza", "amount": "1"})
    assert bad["food_type"] == "other"               # 白名单外落 other，不是崩溃


# ---------------------------------------------------------------- 健康日历聚合

def test_calendar_month_three_sources_and_levels(tmp_db):
    keke = db.fetch_pet_by_name("测测")
    today = date.today()
    iso = today.isoformat()
    db.add_record(keke["id"], {"type": "checkup", "title": "t-today", "next_date": iso})
    # 逾期分级只作用于"到期日落在所查月份"的记录：造在昨天，并查昨天所在月（跨月初也成立）
    yesterday = (today - timedelta(days=1)).isoformat()
    db.add_record(keke["id"], {"type": "vaccine", "title": "t-old", "next_date": yesterday})
    db.add_record(keke["id"], {"type": "clinic", "title": "t-done", "date": iso})
    db.add_medication(keke["id"], {"name": "m-long", "start_date": iso})   # 长期：无 end_date

    cal = db.calendar_month(today.year, today.month)
    td = cal["days"][iso]
    assert any(r["title"] == "t-today" and r["level"] == "soon" and r["days_left"] == 0 for r in td["reminders"])
    assert any(r["title"] == "t-done" for r in td["records"])
    assert any(m["name"] == "m-long" for m in td["meds"])
    # 长期用药展开到月末：本月剩余每天都该有紫点
    import calendar as _cal
    last = date(today.year, today.month, _cal.monthrange(today.year, today.month)[1]).isoformat()
    assert cal["days"][last]["meds"]
    assert cal["summary"]["reminders"] >= 1 and cal["summary"]["med_days"] >= 1

    yd = today - timedelta(days=1)
    cal_prev = db.calendar_month(yd.year, yd.month)
    assert any(r["title"] == "t-old" and r["level"] == "overdue" and r["days_left"] == -1
               for r in cal_prev["days"][yesterday]["reminders"])


def test_calendar_med_null_or_dirty_dates_skipped(tmp_db):
    keke = db.fetch_pet_by_name("两两")
    conn = db.get_conn()
    try:
        conn.execute("INSERT INTO medications(pet_id,name,start_date,end_date,status) VALUES(?,?,?,?,?)",
                     (keke["id"], "m-null", None, None, "active"))          # start NULL 不应被静默丢
        conn.execute("INSERT INTO medications(pet_id,name,start_date,end_date,status) VALUES(?,?,?,?,?)",
                     (keke["id"], "m-dirty", "2026-09-1-", None, "active"))  # 脏日期跳过不 500
        conn.commit()
    finally:
        conn.close()
    cal = db.calendar_month(date.today().year, date.today().month)
    names = [m["name"] for d in cal["days"].values() for m in d["meds"]]
    assert "m-null" in names and "m-dirty" not in names


def test_calendar_med_frequency_semantics(tmp_db):
    """用药频次决定展开方式：每日=疗程内每天；每月一次=只标每月该用的那天；每周一次=只标每周那天。"""
    keke = db.fetch_pet_by_name("两两")
    today = date.today()
    m_first = today.replace(day=1)
    y, m = today.year, today.month
    import calendar as _cal
    m_last = date(y, m, _cal.monthrange(y, m)[1])
    start = (today - timedelta(days=400)).isoformat()          # 久远的开始，覆盖完整周期
    db.add_medication(keke["id"], {"name": "f-daily", "frequency": "每日两次", "start_date": start})
    db.add_medication(keke["id"], {"name": "f-monthly", "frequency": "每月一次", "start_date": start})
    db.add_medication(keke["id"], {"name": "f-weekly", "frequency": "每周一次", "start_date": start})
    cal = db.calendar_month(y, m)

    def days_of(name):
        return sorted(d for d, b in cal["days"].items() for x in b["meds"] if x["name"] == name)

    daily = days_of("f-daily")
    assert len(daily) >= 20 and daily[0] == m_first.isoformat() and daily[-1] <= m_last.isoformat()
    monthly = days_of("f-monthly")
    assert len(monthly) == 1                                    # 只标每月该用的那天（与 start 同日号）
    weekly = days_of("f-weekly")
    assert 4 <= len(weekly) <= 5                                # 一个月 4~5 个周期日
    gaps = {(date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in zip(weekly, weekly[1:])}
    assert gaps == {7}


# ---------------------------------------------------------------- complete_record 幂等与滚动

def test_complete_record_idempotent_and_roll(tmp_db):
    keke = db.fetch_pet_by_name("测测")
    rec = db.add_record(keke["id"], {"type": "deworm", "title": "t", "next_date": "2027-01-31",
                                     "repeat_rule": "monthly"})
    done = db.complete_record(rec["id"])
    assert done["record"]["next_date"] is None
    assert done["next"]["next_date"] == "2027-02-28"   # 月末钳制
    again = db.complete_record(rec["id"])
    assert again["next"] is None                        # 二次完成不再生成
    assert db.list_records(keke["id"])[0]["repeat_rule"] == "monthly"  # 规则随下一轮继承


# ---------------------------------------------------------------- 数据库快照备份

def test_backup_creates_valid_snapshot_and_prunes(tmp_db, tmp_path):
    t1 = db.backup_db()
    assert t1 and os.path.exists(t1)
    snap = sqlite3.connect(t1)                          # 快照是合法 SQLite 且含业务表
    try:
        names = {r[0] for r in snap.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        snap.close()
    assert "pets" in names
    assert db.today_backup_done() is True               # 文件名含今天日期 → 当天判定已备份

    bdir = tmp_path / "backups"
    for i in range(16):                                 # 16 份旧快照（假内容，仅测清理）
        (bdir / f"pets-202001{i:02d}-000000.db").write_bytes(b"x")
    t3 = db.backup_db()
    files = sorted(p.name for p in bdir.glob("pets-*.db"))
    assert len(files) == db.BACKUP_KEEP                 # 只保留最近 14 份
    assert os.path.basename(t3) in files and files[-1] == os.path.basename(t3)


# ---------------------------------------------------------------- 供应商熔断半开恢复

def test_provider_half_open_recovery(monkeypatch):
    """欠费熔断 5 分钟内跳过且 current_provider 不报；满窗口进入半开允许重试；成功后彻底恢复。"""
    import agent
    monkeypatch.setenv("BAILIAN_API_KEY", "sk-test")    # 仅 bailian 有 key，链上唯一候选
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(agent, "_dead_providers", set())
    monkeypatch.setattr(agent, "_dead_since", {})

    agent._trip_provider("bailian")
    assert agent._provider_skippable("bailian") is True
    assert agent.current_provider() is None             # 熔断窗口内视为不可用

    agent._dead_since["bailian"] -= agent._HALF_OPEN_SECS + 1   # 模拟 5 分钟流逝 → 半开
    assert agent._provider_skippable("bailian") is False
    assert agent.current_provider() == "bailian"        # 半开供应商重新参与候选

    agent._revive_provider("bailian")                   # 半开尝试成功 → 彻底恢复
    assert "bailian" not in agent._dead_providers and "bailian" not in agent._dead_since
    assert agent.current_provider() == "bailian"
