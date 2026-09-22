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


# ---------------------------------------------------------------- 回收站（撤销删除）

def test_trash_roundtrip_expense(tmp_db):
    keke = db.fetch_pet_by_name("测测")
    e = db.add_expense({"pet_id": keke["id"], "category": "food", "amount": 66.6, "date": "2026-09-10"})
    tid = db.trash_put("expense", e["id"])
    assert tid is not None
    assert db.delete_expense(e["id"]) is True
    assert db.get_expense(e["id"]) is None
    assert db.restore_from_trash(tid) is True
    back = db.get_expense(e["id"])
    assert back and back["amount"] == 66.6 and back["pet_id"] == keke["id"]   # 原 id 原值回插
    assert db.restore_from_trash(tid) is False          # 快照已消费，二次恢复失败

def test_trash_missing_entity(tmp_db):
    assert db.trash_put("expense", 999999) is None

def test_trash_pet_cascade_and_relink(tmp_db):
    """删宠物：CASCADE 子表进快照回插；SET NULL 的 expenses/memories 靠 relink 认回原主。"""
    keke = db.fetch_pet_by_name("测测")
    rec = db.add_record(keke["id"], {"type": "checkup", "title": "t-cascade"})
    db.add_feeding_log(keke["id"], {"food_type": "kibble", "amount": "50g"})
    exp = db.add_expense({"pet_id": keke["id"], "category": "medical", "amount": 9, "date": "2026-09-09"})
    mem = db.add_memory({"pet_id": keke["id"], "date": "2026-09-01", "title": "m-relink"})
    tid = db.trash_put("pet", keke["id"])
    db.delete_pet(keke["id"])
    assert db.get_pet(keke["id"]) is None
    assert db.get_record(rec["id"]) is None                          # CASCADE 已消失
    assert db.get_expense(exp["id"])["pet_id"] is None               # SET NULL 存活但失去归属
    assert db.restore_from_trash(tid) is True
    assert db.get_pet(keke["id"]) is not None
    assert db.get_record(rec["id"]) is not None                      # 子行按原 id 回插
    assert db.get_expense(exp["id"])["pet_id"] == keke["id"]         # relink 认回原主
    assert db.get_memory(mem["id"])["pet_id"] == keke["id"]


# ---------------------------------------------------------------- 快照恢复

def test_list_and_restore_backup_roundtrip(tmp_db):
    keke = db.fetch_pet_by_name("测测")
    snap1 = db.backup_db()                                           # 快照 A：只有测测两两
    db.add_record(keke["id"], {"type": "vaccine", "title": "after-snap"})
    listings = db.list_backups()
    assert listings and listings[0]["name"].endswith(".db")          # 新→旧排序
    r = db.restore_backup(listings[0]["name"])
    assert r.get("ok") is True and r.get("safety")                   # 恢复前自动做了当前安全快照
    names = {p["name"] for p in db.list_backups()}
    assert snap1.split(os.sep)[-1] in names                          # 原快照还在
    assert db.list_all_records() == []                               # 快照之后写入的数据被回滚掉

def test_restore_backup_rejects_bad_names(tmp_db):
    assert db.restore_backup("../pets.db")["error"]                  # 路径穿越
    assert db.restore_backup("pets-999.db")["error"]                 # 格式不符
    assert db.restore_backup("pets-20200101-000000.db")["error"]     # 名称合法但不存在


# ---------------------------------------------------------------- 药箱

def test_medbox_status_machine(tmp_db):
    t = date.today()
    it = db.medbox_status({"form": "tablet", "expiry_date": (t + timedelta(days=20)).isoformat(), "created_at": t.isoformat()})
    assert it["status"] == "soon" and it["remain_days"] == 20 and 2 <= it["remain_pct"] <= 100 and it["form_label"] == "药片"
    it = db.medbox_status({"form": "other", "expiry_date": (t - timedelta(days=3)).isoformat()})
    assert it["status"] == "expired" and it["remain_pct"] == 2       # 过期钳到最低 2%（满格红是渲染语义，数值仍保底可见）
    it = db.medbox_status({"form": "other"})
    assert it["status"] == "ok" and it["remain_days"] is None and it["remain_pct"] is None
    # 开封推定早于有效期 → 取 min，并判过期
    it = db.medbox_status({"form": "drops", "expiry_date": (t + timedelta(days=300)).isoformat(),
                           "opened_date": (t - timedelta(days=40)).isoformat(), "open_period_days": 30})
    assert it["status"] == "expired" and it["opened"] is True
    assert it["effective_deadline"] == (t - timedelta(days=10)).isoformat()
    # 开封在保质期内：剩 80 天 ok
    it = db.medbox_status({"form": "drops", "opened_date": (t - timedelta(days=10)).isoformat(), "open_period_days": 90,
                           "expiry_date": (t + timedelta(days=500)).isoformat()})
    assert it["status"] == "ok" and it["remain_days"] == 80

def test_medbox_crud_and_pets(tmp_db):
    keke = db.fetch_pet_by_name("测测"); cat = db.fetch_pet_by_name("两两")
    it = db.add_medbox({"name": "益生菌", "form": "粉剂乱填", "qty": 12, "unit": "包", "pet_ids": [keke["id"], cat["id"]]})
    assert it["form"] == "other"                                     # 白名单外落 other
    assert {p["id"] for p in it["pets"]} == {keke["id"], cat["id"]}
    upd = db.update_medbox(it["id"], {"pet_ids": [cat["id"]], "expiry_date": (date.today() + timedelta(days=5)).isoformat()})
    assert [p["id"] for p in upd["pets"]] == [cat["id"]] and upd["status"] == "soon"
    assert [a["id"] for a in db.medbox_attention()] == [it["id"]]
    assert db.add_medbox({"name": "X", "pet_ids": [999999]}) is None  # 不存在的宠物整体拒绝
    assert db.delete_medbox(it["id"]) is True
    assert db.get_medbox(it["id"]) is None and db.list_medbox() == []

def test_medbox_trash_roundtrip(tmp_db):
    keke = db.fetch_pet_by_name("测测")
    it = db.add_medbox({"name": "滴耳液", "form": "drops", "pet_ids": [keke["id"]]})
    tid = db.trash_put("medbox", it["id"])
    db.delete_medbox(it["id"])
    assert db.get_medbox(it["id"]) is None
    assert db.restore_from_trash(tid) is True
    back = db.get_medbox(it["id"])
    assert back and back["name"] == "滴耳液" and [p["id"] for p in back["pets"]] == [keke["id"]]

def test_pet_undo_restores_medbox_links(tmp_db):
    keke = db.fetch_pet_by_name("测测")
    it = db.add_medbox({"name": "驱虫药", "pet_ids": [keke["id"]]})
    tid = db.trash_put("pet", keke["id"])
    db.delete_pet(keke["id"])
    assert db.get_medbox(it["id"])["pets"] == []                     # 关联行被 CASCADE
    assert db.restore_from_trash(tid) is True
    assert [p["id"] for p in db.get_medbox(it["id"])["pets"]] == [keke["id"]]

def test_calendar_expiry_fourth_source(tmp_db):
    t = date.today()
    db.add_medbox({"name": "今天就到期", "expiry_date": t.isoformat()})
    db.add_medbox({"name": "下个月才到期", "expiry_date": (t + timedelta(days=40)).isoformat()})
    cal = db.calendar_month(t.year, t.month)
    assert any(e["name"] == "今天就到期" for e in cal["days"][t.isoformat()]["expiry"])
    assert cal["summary"]["medbox"] >= 1

def test_reset_demo_clears_medbox(tmp_db):
    db.add_medbox({"name": "A"})
    db.reset_demo_data()
    assert db.list_medbox() == []

def test_query_medbox_tool(tmp_db):
    import tools
    keke = db.fetch_pet_by_name("测测")
    db.add_medbox({"name": "皮肤药膏", "form": "ointment", "qty": 1, "unit": "盒", "pet_ids": [keke["id"]],
                   "expiry_date": (date.today() + timedelta(days=10)).isoformat()})
    out = tools.query_medbox()
    assert "皮肤药膏" in out and "临期" in out
    assert "测测" in tools.query_medbox("测测")
    assert "空" in tools.query_medbox.__doc__                        # docstring 兼作 LLM 工具描述

def test_medbox_briefing_fallback(tmp_db):
    import agent
    assert "空" in agent.medbox_briefing_fallback()
    db.add_medbox({"name": "过期药", "expiry_date": (date.today() - timedelta(days=1)).isoformat()})
    t = agent.medbox_briefing_fallback()
    assert "过期药" in t and "已过期" in t

def test_medbox_ocr_and_briefing_error_paths(tmp_db, monkeypatch):
    import agent
    for k in ("BAILIAN_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.setenv(k, "")        # 三链全灭（tmp_db 药箱为空 → 规则版直接出话，不触网）
    assert agent.medbox_ocr("data:image/png;base64,xx")["error"]      # 无供应商 → 统一 error 结构
    b = agent.medbox_briefing()
    assert b["text"] and b["mode"] == "example"                       # LLM 链灭 → 规则版照常出话

def test_medbox_route_words(tmp_db):
    import agent
    assert agent._route("家里药箱还有什么药？")[0] == "health_analyst"
    assert agent._route("这瓶眼药水开封这么久了还能用吗")[0] == "health_analyst"
    assert agent._route("帮我看看药品库存")[0] == "health_analyst"

def test_medbox_validation():
    from main import MedboxIn
    assert MedboxIn(name="药").form == "other" and MedboxIn(name="药").pet_ids == []
    assert MedboxIn(name="药", qty=None).qty is None                  # 余量可显式空
    with pytest.raises(ValidationError):
        MedboxIn(name="")
    with pytest.raises(ValidationError):
        MedboxIn(name="药", qty=-1)
    with pytest.raises(ValidationError):
        MedboxIn(name="药", expiry_date="2026-13-01")               # 月份非法（strptime 容忍非补零，项目一贯语义）
    with pytest.raises(ValidationError):
        MedboxIn(name="药", open_period_days=0)


# ---------------------------------------------------------------- 健康记录附图

def test_record_image_crud(tmp_db):
    keke = db.fetch_pet_by_name("测测")
    r = db.add_record(keke["id"], {"type": "clinic", "title": "t-img", "image": "data:image/jpeg;base64,AAA"})
    assert r["image"] == "data:image/jpeg;base64,AAA"
    # 编辑不传 image → 保留
    assert db.update_record(r["id"], {"type": "clinic", "title": "t-img2"})["image"] == "data:image/jpeg;base64,AAA"
    # 编辑传 '' → 清除
    assert not db.update_record(r["id"], {"type": "clinic", "title": "t-img3", "image": ""})["image"]

def test_record_in_image_validation():
    from main import RecordIn
    assert RecordIn(type="clinic", title="t", image="data:image/png;base64,BBB").image.startswith("data:")
    assert RecordIn(type="clinic", title="t", image="").image == ""
    with pytest.raises(ValidationError):
        RecordIn(type="clinic", title="t", image="<script>bad</script>")   # 非 data:image URI 拒绝

# ---------------------------------------------------------------- AI 巡检员

def _patrol_findings_by_rule(scan: dict) -> dict:
    out: dict = {}
    for f in scan["findings"]:
        out.setdefault(f["rule"], []).append(f)
    return out


def test_patrol_six_rules_trigger_and_deterministic_ids(tmp_db):
    """6 条规则各注入数据触发；id 决定式（同日两次调用一致、含当天日期）；level 与 facts 数字正确。"""
    today = date.today().isoformat()
    keke = db.fetch_pet_by_name("测测"); cc = db.fetch_pet_by_name("两两")
    # ① appetite：测测 近3天3次 vs 此前21天日均2 → warn 下降50%
    for i in range(3):
        db.add_feeding_log(keke["id"], {"food_type": "kibble", "amount": "70 g",
                                        "date": (date.today() - timedelta(days=i)).isoformat()})
    for i in range(3, 24):
        db.add_feeding_log(keke["id"], {"food_type": "kibble", "amount": "70 g",
                                        "date": (date.today() - timedelta(days=i)).isoformat()})
        db.add_feeding_log(keke["id"], {"food_type": "kibble", "amount": "70 g",
                                        "date": (date.today() - timedelta(days=i)).isoformat()})
    # 两两 此前21天每天1次、近3天0次 → high
    for i in range(3, 24):
        db.add_feeding_log(cc["id"], {"food_type": "kibble", "amount": "半罐",
                                      "date": (date.today() - timedelta(days=i)).isoformat()})
    # ② weight_gap：两两 created_at 200天前 + 体重记录 -90 天
    conn = db.get_conn()
    try:
        conn.execute("UPDATE pets SET created_at=? WHERE id=?",
                     ((date.today() - timedelta(days=200)).isoformat() + " 10:00:00", cc["id"]))
        conn.commit()
    finally:
        conn.close()
    db.add_weight_log(cc["id"], {"date": (date.today() - timedelta(days=90)).isoformat(), "weight": 4.5})
    # ⑥ weight_delta：测测 近30天 10→12 kg（+20%）
    db.add_weight_log(keke["id"], {"date": (date.today() - timedelta(days=10)).isoformat(), "weight": 10.0})
    db.add_weight_log(keke["id"], {"date": (date.today() - timedelta(days=1)).isoformat(), "weight": 12.0})
    # ④ overdue：-20 天 → high
    db.add_record(keke["id"], {"type": "vaccine", "title": "狂犬逾期项", "next_date": (date.today() - timedelta(days=20)).isoformat()})
    # ⑤ medbox：昨天过期 → high
    db.add_medbox({"name": "过期药A", "expiry_date": (date.today() - timedelta(days=1)).isoformat()})
    # ③ spend：上月医疗 100、本月 350 → warn（≥3倍且≥100）
    prev_mid = (date.today().replace(day=1) - timedelta(days=1))
    db.add_expense({"pet_id": None, "category": "medical", "amount": 100.0, "date": prev_mid.isoformat()})
    db.add_expense({"pet_id": None, "category": "medical", "amount": 350.0, "date": today})

    s1 = db.patrol_scan(); s2 = db.patrol_scan()
    assert s1["rule_errors"] == [] and s1["scanned"] == 6
    assert [f["id"] for f in s1["findings"]] == [f["id"] for f in s2["findings"]]   # 同日决定式一致
    by = _patrol_findings_by_rule(s1)
    assert all(today in f["id"] for f in s1["findings"])                            # id 含当天日期
    ap_high = [f for f in by["appetite"] if f["id"].startswith(f"appetite:{cc['id']}")][0]
    assert ap_high["level"] == "high" and "没有任何进食记录" in ap_high["title"]
    ke_ap = [f for f in by["appetite"] if f["id"].startswith(f"appetite:{keke['id']}")][0]
    assert ke_ap["level"] == "warn" and "下降50%" in ke_ap["title"]
    assert {"k": "近3天喂餐", "v": "3 次"} in ke_ap["facts"] and {"k": "此前均值", "v": "2.0 次/天"} in ke_ap["facts"]
    assert by["weight_gap"][0]["level"] == "warn" and "90 天" in by["weight_gap"][0]["title"]
    assert by["spend"][0]["level"] == "warn" and by["spend"][0]["id"] == f"spend:0:{today}"
    assert by["overdue"][0]["level"] == "high" and "20 天" in by["overdue"][0]["title"]
    assert by["medbox"][0]["level"] == "high" and by["medbox"][0]["facts"][0]["k"] == "过期药A"
    wd = by["weight_delta"][0]
    assert wd["level"] == "warn" and "20.0%" in wd["title"] and wd["link"] == {"view": "detail", "opts": {"petId": keke["id"]}}
    # 排序 high→warn→info
    ranks = [{"high": 0, "warn": 1, "info": 2}[f["level"]] for f in s1["findings"]]
    assert ranks == sorted(ranks)


def test_patrol_rule_isolation_bad_row(tmp_db):
    """脏 next_date 只坏 overdue 一条：rule_errors 计数，其余规则照常出 findings。"""
    keke = db.fetch_pet_by_name("测测")
    conn = db.get_conn()
    try:
        conn.execute("INSERT INTO health_records(pet_id,type,date,title,next_date) VALUES(?,?,?,?,?)",
                     (keke["id"], "checkup", "2026-01-01", "脏日期", "2026-99-99"))
        conn.commit()
    finally:
        conn.close()
    db.add_medbox({"name": "隔离药", "expiry_date": (date.today() - timedelta(days=1)).isoformat()})
    s = db.patrol_scan()
    assert s["rule_errors"] and any("overdue" in e for e in s["rule_errors"])
    assert all(f["rule"] != "overdue" for f in s["findings"])       # 坏规则不产出，也不拖垮整体
    assert any(f["rule"] == "medbox" for f in s["findings"])        # 其他规则正常出 finding


def test_patrol_no_pets_empty(tmp_db):
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM pets")
        conn.commit()
    finally:
        conn.close()
    s = db.patrol_scan()
    assert s["findings"] == [] and s["rule_errors"] == []


def test_patrol_report_rules_mode_and_cache_hit(tmp_db, monkeypatch):
    """无 LLM：mode=rules、ai_text=模板原文、fresh False→True（当天二次命中缓存）、history 有当日条目。"""
    import agent
    for k in ("BAILIAN_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.setenv(k, "")
    keke = db.fetch_pet_by_name("测测")
    db.add_record(keke["id"], {"type": "vaccine", "title": "逾期项", "next_date": (date.today() - timedelta(days=20)).isoformat()})
    r1 = agent.patrol_report()
    assert r1["mode"] == "rules" and r1["fresh"] is False and r1["level"] == "high"
    assert r1["scanned"] == 6 and r1["findings"]
    assert all(f["ai_text"] == f["title"] for f in r1["findings"])  # 模板回落
    assert any(h["date"] == date.today().isoformat() for h in r1["history"])
    r2 = agent.patrol_report()
    assert r2["fresh"] is True and r2["level"] == "high"


def test_patrol_budget_gate(tmp_db, monkeypatch):
    """润色总失败：budget 当日 +1 封顶 2，第 3 次（sig 变化）不再调用润色。"""
    import agent
    calls = {"n": 0}
    def failing_polish(findings, force=False):
        calls["n"] += 1
        return {"ok": False}
    class SyncThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._t, self._a = target, args
        def start(self):
            self._t(*self._a)
    monkeypatch.setattr(agent, "patrol_polish", failing_polish)
    monkeypatch.setattr(agent, "_llm_candidates", lambda: ["fake"])
    monkeypatch.setattr(agent.threading, "Thread", SyncThread)
    keke = db.fetch_pet_by_name("测测")
    for i in range(3):   # 每轮加一条逾期记录 → 标题变 → sig 变 → 触发新一轮
        db.add_record(keke["id"], {"type": "vaccine", "title": f"逾期{i}", "next_date": (date.today() - timedelta(days=20 + i)).isoformat()})
        agent.patrol_report()
    assert calls["n"] == 2                                   # 第 3 次被预算闸挡下
    assert db.kv_get(f"patrol:budget:{date.today().isoformat()}") == "2"


def test_patrol_single_ticket(tmp_db, monkeypatch):
    """并发票：桩 polish sleep 200ms，连发 3 次 patrol_report（sig 变），实际只执行 1 个润色任务。"""
    import agent, time
    executed = {"n": 0}
    def slow_polish(findings, force=False):
        executed["n"] += 1
        time.sleep(0.2)
        return {"ok": False}
    monkeypatch.setattr(agent, "patrol_polish", slow_polish)
    monkeypatch.setattr(agent, "_llm_candidates", lambda: ["fake"])
    keke = db.fetch_pet_by_name("测测")
    for i in range(3):
        db.add_record(keke["id"], {"type": "vaccine", "title": f"票{i}", "next_date": (date.today() - timedelta(days=25 + i)).isoformat()})
        agent.patrol_report()
    deadline = time.time() + 5
    while agent._patrol_generating and time.time() < deadline:
        time.sleep(0.02)
    assert executed["n"] == 1
    assert db.kv_get(f"patrol:budget:{date.today().isoformat()}") == "1"


def test_patrol_validate_anti_tamper(tmp_db):
    """纯函数校验：编造数字的 prose 丢弃；未知 finding_id 丢弃；合法 prose/insight 通过。"""
    import agent
    fid = "appetite:1:" + date.today().isoformat()
    f = [{"id": fid, "rule": "appetite", "level": "warn",
          "title": "测测 · 近3天食欲下降40%",
          "facts": [{"k": "近3天喂餐", "v": "4 次"}, {"k": "此前均值", "v": "6.7 次/天"}]}]
    bad = {"prose": [{"finding_id": fid, "text": "近3天只喂了5次，降幅40%，建议观察。"},
                     {"finding_id": "不存在的id", "text": "数字1 没问题"}],
           "insight": {"text": "可乐体重下降10%，请就医。"}}
    prose, insight = agent._patrol_validate(bad, f)
    assert prose == {} and insight == ""                     # 5 与 10 越界、未知 id 全丢
    good = {"prose": [{"finding_id": fid, "text": "近3天喂餐4次，低于此前日均6.7次，建议留意。"}],
            "insight": {"text": "近3天喂餐4次，先观察食欲变化。"}}
    prose2, insight2 = agent._patrol_validate(good, f)
    assert prose2[fid].startswith("近3天喂餐4次")
    assert insight2 == "近3天喂餐4次，先观察食欲变化。"

def test_patrol_env_failure_not_counted_self_heal(tmp_db, monkeypatch):
    """402/链灭属环境性失败：不计当日停用计数（充值恢复后手动刷新可自愈）；
    手动刷新在模板态缓存上强制重试润色（带 force 旁路停用）。"""
    import agent
    fid = "appetite:1:" + date.today().isoformat()
    f = [{"id": fid, "rule": "appetite", "level": "warn", "title": "测测 · 近3天食欲下降40%",
          "facts": [{"k": "近3天喂餐", "v": "4 次"}]}]
    today = agent._patrol_today()
    # 环境灭：stub 返回 402 文案 → ok False 且 fail 计数保持 0
    monkeypatch.setattr(agent, "_patrol_llm", lambda p: "⚠️ 巡检润色调用失败（APIStatusError: Error code: 402 - Insufficient Balance）")
    assert agent.patrol_polish(f)["ok"] is False
    assert agent._patrol_kv_int(f"patrol:fail:{today}") == 0
    # 质量坏：坏 JSON → 计数 +1；连 2 次停用，但 force（手动刷新）旁路
    monkeypatch.setattr(agent, "_patrol_llm", lambda p: "⚠️ 巡检润色调用失败（TimeoutError: 超时）")
    agent.patrol_polish(f); agent.patrol_polish(f)
    assert agent._patrol_kv_int(f"patrol:fail:{today}") == 2
    assert agent.patrol_polish(f).get("disabled") is True          # 自动路径停用
    monkeypatch.setattr(agent, "_patrol_llm",
                        lambda p: '{"prose":[{"finding_id":"' + fid + '","text":"近3天喂餐4次，建议留意。"}],"insight":{"text":""}}')
    assert agent.patrol_polish(f, force=True)["ok"] is True        # 手动强制：绕过停用并成功清零
    assert agent._patrol_kv_int(f"patrol:fail:{today}") == 0
    # 模板态缓存上的手动刷新不再短路 fresh（充值自愈路径）
    import json
    import db as _db
    _db.kv_set(agent.PATROL_CACHE_KEY, json.dumps(
        {"date": today, "sig": agent._patrol_sig(f), "prose_map": {}, "insight": {"text": "", "mode": "rules"}, "mode": "rules"}, ensure_ascii=False))
    r = agent.patrol_manual_refresh()
    assert r.get("fresh") is not True                              # rules 缓存不被当已润色


# ---------------------------------------------------------------- 门禁回归（code review 🔴🟡 锁行为）

def test_patrol_appetite_single_meal_triggers(tmp_db):
    """🟡-1 边界回归：近3天恰好1次（骤降83%）也必须触发 warn——曾被 >=2 门槛吞掉。"""
    import agent
    keke = db.fetch_pet_by_name("测测")
    t = date.today()
    for i in range(3, 24):        # 前21天每天1条 → 日均1
        db.add_feeding_log(keke["id"], {"food_type": "kibble", "amount": "x", "date": (t - timedelta(days=i)).isoformat()})
    db.add_feeding_log(keke["id"], {"food_type": "kibble", "amount": "x", "date": (t - timedelta(days=1)).isoformat()})  # 近3天仅1次（降幅67%）
    findings = [f for f in db.patrol_scan()["findings"] if f["rule"] == "appetite"]
    assert findings and findings[0]["level"] == "warn" and "下降" in findings[0]["title"]


def test_medbox_update_clears_dates(tmp_db):
    """🟡-2：update 显式传 null 清空有效期/开封日期（不传=保留，传null=清除）。"""
    keke = db.fetch_pet_by_name("测测")
    it = db.add_medbox({"name": "滴剂", "form": "drops", "expiry_date": "2027-01-01",
                        "opened_date": "2026-09-01", "pet_ids": [keke["id"]]})
    keep = db.update_medbox(it["id"], {"name": "滴剂2"})
    assert keep["expiry_date"] == "2027-01-01"                      # 不传 → 保留
    cleared = db.update_medbox(it["id"], {"expiry_date": None, "opened_date": None})
    assert cleared["expiry_date"] is None and cleared["opened_date"] is None
    assert cleared["status"] == "ok"                                # 清空后回到不限期


def test_patrol_env_failure_refunds_budget(tmp_db, monkeypatch):
    """🔴-2：环境性失败（402/链灭，零 token 成本）退回预算且链灭不占票——充值当天自愈额度不被掐死。"""
    import agent
    class SyncThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._t, self._a = target, args
        def start(self):
            self._t(*self._a)
    keke = db.fetch_pet_by_name("测测")
    db.add_record(keke["id"], {"type": "vaccine", "title": "逾期回归", "next_date": (date.today() - timedelta(days=30)).isoformat()})
    monkeypatch.setattr(agent, "_llm_candidates", lambda: ["fake"])
    monkeypatch.setattr(agent, "_provider_skippable", lambda p: False)
    monkeypatch.setattr(agent.threading, "Thread", SyncThread)
    # 402 假文案 → env 分支退款
    monkeypatch.setattr(agent, "_patrol_llm", lambda p: "⚠️ 巡检润色调用失败（APIStatusError: Error code: 402 - Insufficient Balance）")
    agent.patrol_report()
    bkey = f"patrol:budget:{date.today().isoformat()}"
    assert db.kv_get(bkey) in (None, "0")                           # 占而复退，额度还在
    # 链灭（无活供应商）：连开 N 次页面不占票不占预算
    monkeypatch.setattr(agent, "_llm_candidates", lambda: [])
    for _ in range(4):
        agent.patrol_report()
    assert db.kv_get(bkey) in (None, "0")
    monkeypatch.setattr(agent, "_llm_candidates", lambda: ["fake"])
    r = agent.patrol_manual_refresh()
    assert r.get("capped") is not True                              # 充值恢复后手动刷新仍有全额预算可用


# ---------------------------------------------------------------- 主人养成教练（规则层）

def test_coach_dims_and_grade_bounds(tmp_db):
    dims = db.coach_dims()
    assert len(dims) == 8
    keys = [d["key"] for d in dims]
    assert keys == [k for k, _l, _w in db.COACH_DIM_DEFS]
    for d in dims:
        assert 0 <= d["score"] <= 100
        assert "label" in d and "note" in d
    assert db.coach_grade(100) == "S" and db.coach_grade(95) == "S"
    assert db.coach_grade(85) == "A" and db.coach_grade(70) == "B" and db.coach_grade(0) == "C"


def test_coach_weekly_tasks_once_and_done(tmp_db):
    w1 = db.coach_weekly()
    assert "week" in w1 and w1["tasks"], "低分维应派生任务"
    n = len(w1["tasks"])
    w2 = db.coach_weekly()
    assert len(w2["tasks"]) == n, "同周不得重复派发"
    tid = w2["tasks"][0]["id"]
    assert db.coach_task_done(tid)["status"] == "done"
    w3 = db.coach_weekly()
    assert all(t["id"] != tid or t["status"] == "done" for t in w3["tasks"])
    assert len(w3["tasks"]) == n


def test_coach_score_covers_ledger_diet(tmp_db):
    keke = db.fetch_pet_by_name("测测")
    # 无饮食无记账 → diet/ledger 应低分并进 note
    dims = {d["key"]: d for d in db.coach_dims()}
    assert dims["diet"]["score"] < 85 and dims["diet"]["note"]
    assert dims["ledger"]["score"] < 85
    db.add_expense({"pet_id": keke["id"], "category": "food", "amount": 10, "date": date.today().isoformat()})
    db.add_feeding_log(keke["id"], {"food_type": "kibble", "amount": "1", "date": date.today().isoformat()})
    db.add_feeding_log(keke["id"], {"food_type": "kibble", "amount": "1", "date": date.today().isoformat()})
    db.add_feeding_log(keke["id"], {"food_type": "kibble", "amount": "1", "date": date.today().isoformat()})
    dims2 = {d["key"]: d for d in db.coach_dims()}
    assert dims2["diet"]["score"] >= 75
    assert dims2["ledger"]["score"] >= 80


def test_coach_letter_fallback_mentions_score(tmp_db):
    import agent
    payload = db.coach_weekly()
    text = agent.coach_letter_fallback(payload)
    assert str(payload["score"]) in text
    assert payload["grade"] in text
    letter = agent.coach_letter(payload)
    assert letter["text"] and letter["mode"] in ("rules", "agent")
