# -*- coding: utf-8 -*-
"""tools.py — 五个 Agent 工具（纯函数实现，LangChain 与降级模式共用）。"""
import json

import db


def _pet_missing_text(name: str) -> str:
    pets = db.list_pets()
    names = "、".join(p["name"] for p in pets) if pets else "（暂无宠物）"
    return f"未在数据库找到名为「{name}」的宠物。当前系统中的宠物有：{names}。"


def query_pet(name: str) -> str:
    """按名字查询宠物的基本信息（品种/年龄/体重/状态/性格）。"""
    pet = db.fetch_pet_by_name(name)
    if not pet:
        return _pet_missing_text(name)
    info = {
        "名字": pet["name"],
        "类型": db.PET_TYPES.get(pet["type"], pet["type"]),
        "品种": pet["breed"] or "未知",
        "性别": db.GENDERS.get(pet["gender"], pet["gender"] or "未知"),
        "生日": pet["birthday"] or "未知",
        "年龄": pet["age"],
        "当前体重(kg)": pet["latest_weight"],
        "健康状态": db.PET_STATUS.get(pet["status"], pet["status"]),
        "性格": pet["personality"] or "无备注",
        "健康记录数": pet["record_count"],
    }
    return f"「{pet['name']}」的基本信息：" + json.dumps(info, ensure_ascii=False)


def query_health_records(name: str) -> str:
    """查询某只宠物的全部健康记录（疫苗/体检/驱虫/喂药/就诊），按时间倒序。"""
    pet = db.fetch_pet_by_name(name)
    if not pet:
        return _pet_missing_text(name)
    records = db.list_records(pet["id"])
    if not records:
        return f"「{pet['name']}」暂无健康记录。"
    lines = [f"「{pet['name']}」共 {len(records)} 条健康记录（按时间倒序）："]
    for r in records:
        line = f"- {r['date']}【{r['type_label']}】{r['title']}"
        if r.get("note"):
            line += f"（{r['note']}）"
        if r.get("next_date"):
            d = r["days_left"]
            if d < 0:
                line += f"；下次日期 {r['next_date']}，已逾期 {-d} 天"
            elif d <= 7:
                line += f"；下次日期 {r['next_date']}，还剩 {d} 天"
            else:
                line += f"；下次日期 {r['next_date']}"
            if r.get("repeat_label"):
                line += f"（{r['repeat_label']}重复）"
        lines.append(line)
    return "\n".join(lines)


def get_reminders(_input: str = "") -> str:
    """列出所有临期（≤7天）或已逾期的事项。无需参数。"""
    rem = db.compute_reminders()
    if not rem:
        return "当前没有临期或逾期的事项，一切都在计划内。"
    lines = [f"共 {len(rem)} 项需要关注："]
    for r in rem:
        rep = f"，{r['repeat_label']}重复" if r.get("repeat_label") else ""
        if r["overdue"]:
            lines.append(f"- ⚠️ 已逾期 {-r['days_left']} 天：{r['pet_name']} 的"
                         f"【{r['type_label']}】{r['title']}（应于 {r['next_date']}{rep}）")
        else:
            lines.append(f"- ⏰ 还剩 {r['days_left']} 天：{r['pet_name']} 的"
                         f"【{r['type_label']}】{r['title']}（下次日期 {r['next_date']}{rep}）")
    return "\n".join(lines)


def query_memories(name: str = "") -> str:
    """查询宠物手动记录的回忆故事（成长记录/纪念时刻）。名字留空时返回全部宠物的最新回忆。"""
    import db
    name = (name or "").strip()
    if name:
        pet = db.fetch_pet_by_name(name)
        if not pet:
            return _pet_missing_text(name)
        mems = db.list_memories(pet["id"])
        if not mems:
            return f"「{pet['name']}」还没有记录任何回忆，可以在「回忆集」页面添加第一条。"
        days = db.days_together(pet)
        lines = [f"「{pet['name']}」共 {len(mems)} 条回忆"
                 + (f"，不知不觉已经陪伴了 {days} 天：" if days else "：")]
        for m in mems:
            line = f"- {m['date']}《{m['title']}》"
            if m.get("text"):
                line += f"：{m['text']}"
            lines.append(line)
        return "\n".join(lines)
    mems = db.list_memories()[:10]
    if not mems:
        return "还没有任何回忆记录，可以在「回忆集」页面为宠物写下第一个故事。"
    lines = [f"最近的 {len(mems)} 条回忆（按时间倒序）："]
    for m in mems:
        who = m["pet_name"] or "未指定宠物"
        lines.append(f"- {m['date']}【{who}】《{m['title']}》" + (f"：{m['text']}" if m.get("text") else ""))
    return "\n".join(lines)


def query_medications(name: str) -> str:
    """查询某只宠物的用药情况：在用药物（药名/剂量/频次/疗程与剩余天数）与已结束的用药历史。"""
    pet = db.fetch_pet_by_name(name)
    if not pet:
        return _pet_missing_text(name)
    meds = db.list_medications(pet["id"])
    if not meds:
        return f"「{pet['name']}」目前没有任何用药记录。"
    active = [m for m in meds if m["status"] == "active"]
    done = [m for m in meds if m["status"] != "active"]
    lines = [f"「{pet['name']}」用药情况：在用 {len(active)} 项，已结束 {len(done)} 项。"]
    if active:
        lines.append("在用：")
        for m in active:
            line = f"- {m['name']}"
            detail = "，".join(x for x in (m.get("dosage"), m.get("frequency")) if x)
            if detail:
                line += f"（{detail}）"
            line += f"，自 {m['start_date']} 起"
            if m.get("end_date"):
                d = m["days_left"]
                line += (f"，计划至 {m['end_date']}，" +
                         (f"还剩 {d} 天" if d >= 0 else f"已超出计划 {-d} 天，建议确认是否停药或复诊"))
            else:
                line += "，长期/未设结束日期"
            if m.get("note"):
                line += f"；备注：{m['note']}"
            lines.append(line)
    if done:
        lines.append("已结束：")
        for m in done[:5]:
            span = f"{m['start_date']}" + (f" 至 {m['end_date']}" if m.get("end_date") else "")
            lines.append(f"- {m['name']}（{span}）" + (f"：{m['note']}" if m.get("note") else ""))
    return "\n".join(lines)


def analyze_health(name: str) -> str:
    """结合健康记录、体重历史与在用药物，对某只宠物做简要健康分析。"""
    pet = db.fetch_pet_by_name(name)
    if not pet:
        return _pet_missing_text(name)
    records = db.list_records(pet["id"])
    weights = db.list_weight_logs(pet["id"])
    meds = [m for m in db.list_medications(pet["id"]) if m["status"] == "active"]
    today = db.today_str()
    recent = [r for r in records if r["date"] and r["date"] >= _minus_days(today, 90)]

    by_type: dict[str, int] = {}
    for r in records:
        label = db.RECORD_TYPES.get(r["type"], r["type"])
        by_type[label] = by_type.get(label, 0) + 1
    rem = [x for x in db.compute_reminders() if x["pet_id"] == pet["id"]]

    lines = [f"「{pet['name']}」健康分析："]
    lines.append(f"- 基本状态：{db.PET_STATUS.get(pet['status'], pet['status'])}"
                 f"（{pet['age']}，当前体重 {pet['latest_weight']}kg）")
    lines.append(f"- 记录概况：累计 {len(records)} 条"
                 + ("，近90天 " + str(len(recent)) + " 条" if records else "")
                 + (f"（{'·'.join(f'{k}{v}次' for k, v in by_type.items())}）" if by_type else ""))
    if not records:
        lines.append("- ⚠️ 尚无任何健康记录，建议尽快安排一次基础体检与疫苗确认。")
    elif rem:
        overdue = [x for x in rem if x["overdue"]]
        if overdue:
            lines.append(f"- ⚠️ 有 {len(overdue)} 项已逾期：" +
                         "；".join(f"【{x['type_label']}】{x['title']}" for x in overdue))
        else:
            lines.append("- ⏰ 临期项目：" +
                         "；".join(f"【{x['type_label']}】{x['title']}（剩{x['days_left']}天）" for x in rem))
    else:
        lines.append("- ✅ 没有临期/逾期项目。")
    last_rec = records[0] if records else None
    if last_rec:
        gap = db.days_until(last_rec["date"])
        lines.append(f"- 最近一次记录：{last_rec['date']}【{last_rec['type_label']}】{last_rec['title']}"
                     + (f"（{-gap} 天前）" if gap < 0 else ""))
        if -gap > 60:
            lines.append("- ⚠️ 已超过 60 天没有新的健康记录，建议关注。")
    if len(weights) >= 2:
        delta = weights[-1]["weight"] - weights[0]["weight"]
        pct = (delta / weights[0]["weight"] * 100) if weights[0]["weight"] else 0
        trend = "上升" if delta > 0.005 else ("下降" if delta < -0.005 else "平稳")
        lines.append(f"- 体重变化：从 {weights[0]['weight']}kg → {weights[-1]['weight']}kg"
                     f"（{weights[0]['date']} 至 {weights[-1]['date']}，{trend} {abs(round(pct,1))}%）")
        if abs(pct) >= 10:
            lines.append("- ⚠️ 体重波动超过 10%，建议咨询医生。")
    else:
        lines.append("- 体重变化：记录不足两点，暂无趋势。")
    if meds:
        overdue_meds = [m for m in meds if m.get("end_date") and m["days_left"] is not None and m["days_left"] < 0]
        lines.append(f"- 在用药物 {len(meds)} 项：" + "；".join(
            f"{m['name']}" + (f"（{m['frequency']}）" if m.get("frequency") else "") for m in meds))
        if overdue_meds:
            lines.append("- ⚠️ 有药物已超出计划疗程：" + "、".join(m["name"] for m in overdue_meds) + "，建议确认是否停药或复诊。")
    feed = db.feeding_summary(pet["id"])
    if feed["by_type_30d"]:
        total_feed = sum(feed["by_type_30d"].values())
        dist = "、".join(f"{db.FEEDING_TYPES.get(k, k)}{v}次"
                        for k, v in sorted(feed["by_type_30d"].items(), key=lambda kv: -kv[1]))
        lines.append(f"- 饮食：今日 {feed['today']} 顿，本周 {feed['week']} 次；近 30 天 {dist}")
        treats = feed["by_type_30d"].get("treat", 0)
        if total_feed and treats / total_feed >= 0.3:
            lines.append("- ⚠️ 零食占比 ≥30%，若正在控制体重建议减少零食。")
    return "\n".join(lines)


def _minus_days(d: str, n: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.strptime(d, "%Y-%m-%d").date() - timedelta(days=n)).isoformat()


def generate_report(name: str = "", period: str = "月") -> str:
    """生成结构化健康报告（Markdown）。name 为空时输出全部宠物概览。"""
    if name and name.strip():
        pet = db.fetch_pet_by_name(name)
        if not pet:
            return _pet_missing_text(name)
        return _report_one(pet, period)
    pets = db.list_pets()
    if not pets:
        return "系统中暂无宠物，无法生成报告。"
    period_word = {"周": "一周", "月": "一个月", "年": "一年"}.get((period or "月").strip(), "一个月")
    parts = [f"# 全体宠物健康报告（近{period_word}）", ""]
    for p in pets:
        parts.append(_report_one(p, period, heading_level=2))
        parts.append("")
    return "\n".join(parts).strip()


def _report_one(pet: dict, period: str, heading_level: int = 1) -> str:
    from datetime import datetime, timedelta
    win_days = {"周": 7, "月": 30, "年": 365}.get((period or "月").strip(), 30)
    start = (datetime.now() - timedelta(days=win_days)).strftime("%Y-%m-%d")
    records = db.list_records(pet["id"])
    in_win = [r for r in records if r["date"] and r["date"] >= start]
    weights = db.list_weight_logs(pet["id"])
    rem = [x for x in db.compute_reminders() if x["pet_id"] == pet["id"]]
    overdue = [x for x in rem if x["overdue"]]
    upcoming = [x for x in rem if not x["overdue"]]

    by_type: dict[str, int] = {}
    for r in in_win:
        label = db.RECORD_TYPES.get(r["type"], r["type"])
        by_type[label] = by_type.get(label, 0) + 1

    h = "#" * heading_level
    lines = [f"{h} {pet['name']} 健康报告（近{win_days}天）", ""]
    lines.append(f"- 基本信息：{db.PET_TYPES.get(pet['type'], pet['type'])} · "
                 f"{pet['breed'] or '品种未知'} · {db.GENDERS.get(pet['gender'], '未知')} · "
                 f"{pet['age']} · 当前体重 {pet['latest_weight']}kg")
    lines.append(f"- 当前状态：{db.PET_STATUS.get(pet['status'], pet['status'])}")
    lines.append(f"- 本期健康记录：{len(in_win)} 条"
                 + (f"（{' · '.join(f'{k}{v}' for k, v in by_type.items())}）" if by_type else ""))
    for r in in_win[:6]:
        lines.append(f"  - {r['date']}【{r['type_label']}】{r['title']}")
    if overdue:
        lines.append("- ⚠️ 逾期事项：" + "；".join(
            f"【{x['type_label']}】{x['title']}（逾期{-x['days_left']}天）" for x in overdue))
    if upcoming:
        lines.append("- ⏰ 临期事项：" + "；".join(
            f"【{x['type_label']}】{x['title']}（剩{x['days_left']}天）" for x in upcoming))
    if not rem:
        lines.append("- 到期提醒：无临期/逾期项目 ✅")
    if len(weights) >= 2:
        w0, w1 = weights[0], weights[-1]
        delta = w1["weight"] - w0["weight"]
        lines.append(f"- 体重变化：{w0['date']} {w0['weight']}kg → {w1['date']} {w1['weight']}kg"
                     f"（{'+' if delta >= 0 else ''}{round(delta, 2)}kg）")
    else:
        lines.append("- 体重变化：数据不足")
    # 结论与建议（规则生成）
    advice = []
    if overdue:
        advice.append(f"有 {len(overdue)} 项已逾期，建议尽快补做")
    if upcoming:
        advice.append(f"{len(upcoming)} 项即将到期，请提前预约")
    if not records:
        advice.append("尚无健康记录，建议从基础体检与疫苗核查开始")
    elif len(weights) >= 2 and abs((weights[-1]["weight"] - weights[0]["weight"])
                                   / (weights[0]["weight"] or 1)) >= 0.1:
        advice.append("体重波动较大，建议关注饮食与就医检查")
    if pet["status"] == "ill":
        advice.append("当前处于治疗中，请遵医嘱复诊")
    elif pet["status"] == "attention":
        advice.append("当前状态为需关注，保持观察并按时记录")
    if not advice:
        advice.append("整体状况良好，继续保持定期记录")
    lines.append(f"- 结论与建议：{'；'.join(advice)}。")
    return "\n".join(lines)


def get_care_guide(name: str = "") -> str:
    """查询物种护理规范：该物种适用的记录类型、该做的事、不该做的事（禁忌）与常见疾病。
    name 可传宠物名（自动识别其类型）或直接传类型词（如：鱼/鸟类/fish），留空返回概览。"""
    import species
    from db import PET_TYPES
    q = (name or "").strip()
    if q:
        pet = db.fetch_pet_by_name(q)
        if pet:
            return species.care_guide_text(pet["type"], pet["name"])
        # 非宠物名：尝试按类型词匹配（支持中文标签或英文 key）
        for key, sp in species.SPECIES.items():
            if q in (key, sp["label"]) or q in PET_TYPES and PET_TYPES.get(q) == sp["label"]:
                return species.care_guide_text(key)
        return (f"未找到名为「{q}」的宠物，也不是有效的类型词（可用："
                + "、".join(sp["label"] for sp in species.SPECIES.values()) + "）。")
    lines = ["物种护理规范概览："]
    for key, sp in species.SPECIES.items():
        lines.append(f"- {sp['label']}：适用记录 {'、'.join(sp['record_types'])}"
                     f"；禁忌 {sp['care_dont'][0]}")
    return "\n".join(lines)


DRAFT_MARKER = "@@DRAFT@@"

# 最近一次起草的草稿（/api/chat 取走后附到响应，取走即清空）
LAST_DRAFT: dict | None = None


def take_last_draft() -> dict | None:
    global LAST_DRAFT
    d = LAST_DRAFT
    LAST_DRAFT = None
    return d


# ---------------------------------------------------------------- 一句话记账（规则解析）
# 纯规则、零延迟；LLM 只在缺金额时由 agent.expense_parse 补全（失败不影响表单）。

_EXP_CAT_KEYWORDS = (
    ("medical", ("疫苗", "打针", "狂犬", "驱虫", "体检", "看病", "就诊", "手术", "住院", "药", "耳药", "益生菌", "绝育", "化验", "拍片", "输液")),
    ("food", ("猫粮", "狗粮", "粮食", "主粮", "罐头", "零食", "冻干", "生骨肉", "猫条", "狗零食", "奶", "营养膏", "化毛膏")),
    ("supply", ("猫砂", "尿垫", "玩具", "笼", "窝", "牵引", "项圈", "碗", "指甲", "梳", "航空箱", "猫抓", "垫", "清洁", "湿巾", "尿")),
    ("grooming", ("洗澡", "洗护", "美容", "剪毛", "剃毛", "spa", "SPA", "修剪")),
)


def parse_expense_text(text: str) -> dict:
    """把「可乐打狂犬 280」式口语解析成花销草稿字段（不入库）。
    返回 {amount, category, pet_id, pet_name, date, note, hints:[缺失提示]}。"""
    import re as _re
    from datetime import date, timedelta
    raw = (text or "").strip()
    out = {"amount": None, "category": "other", "pet_id": None, "pet_name": None,
           "date": db.today_str(), "note": raw, "hints": []}
    if not raw:
        out["hints"].append("empty")
        return out

    # 金额：优先「数字+元」，否则最后一个合理数字（排除日期里的日号）
    m = _re.search(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|圆|rmb|RMB|¥)", raw)
    if not m:
        nums = [float(x) for x in _re.findall(r"\d+(?:\.\d{1,2})?", raw)]
        nums = [n for n in nums if 0.01 <= n <= 99999]
        amount = nums[-1] if nums else None
    else:
        amount = float(m.group(1))
    if amount is not None and amount > 0:
        out["amount"] = round(amount, 2)
    else:
        out["hints"].append("amount")

    # 宠物：最长名优先（避免「可」误匹配）
    pets = sorted(db.list_pets(), key=lambda p: -len(p.get("name") or ""))
    for p in pets:
        name = (p.get("name") or "").strip()
        if name and name in raw:
            out["pet_id"], out["pet_name"] = p["id"], name
            break

    # 分类：关键词投票
    scores = {k: 0 for k in db.EXPENSE_CATEGORIES}
    for cat, kws in _EXP_CAT_KEYWORDS:
        for kw in kws:
            if kw in raw:
                scores[cat] += 1
    best = max(scores, key=lambda k: scores[k])
    if scores[best] > 0:
        out["category"] = best
    else:
        out["hints"].append("category")

    # 日期：今天/昨天/前天 / N天前 / X月Y日 / YYYY-MM-DD
    today = date.today()
    if "前天" in raw:
        out["date"] = (today - timedelta(days=2)).isoformat()
    elif "昨天" in raw or "昨晚" in raw:
        out["date"] = (today - timedelta(days=1)).isoformat()
    elif "今天" in raw or "今晚" in raw:
        out["date"] = today.isoformat()
    else:
        dm = _re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
        if dm:
            out["date"] = f"{int(dm.group(1)):04d}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
        else:
            md = _re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日?", raw)
            if md:
                mo, dd = int(md.group(1)), int(md.group(2))
                if 1 <= mo <= 12 and 1 <= dd <= 31:
                    try:
                        out["date"] = date(today.year, mo, dd).isoformat()
                    except ValueError:
                        pass
            else:
                nd = _re.search(r"(\d+)\s*天前", raw)
                if nd:
                    out["date"] = (today - timedelta(days=int(nd.group(1)))).isoformat()

    # 备注：去掉金额与宠物名后的短语；过短则保留原文
    note = raw
    if m:
        note = note.replace(m.group(0), " ")
    if out["pet_name"]:
        note = note.replace(out["pet_name"], " ")
    note = _re.sub(r"\s+", " ", note).strip(" ，,、的花了花在买")
    out["note"] = (note or raw)[:200]
    return out


def _parse_pet_and_date(raw: str) -> tuple:
    """共用：从口语中抠宠物 id/名 与 本地日期。"""
    import re as _re
    from datetime import date, timedelta
    pet_id, pet_name = None, None
    for p in sorted(db.list_pets(), key=lambda x: -len(x.get("name") or "")):
        name = (p.get("name") or "").strip()
        if name and name in raw:
            pet_id, pet_name = p["id"], name
            break
    today = date.today()
    d = today.isoformat()
    if "前天" in raw:
        d = (today - timedelta(days=2)).isoformat()
    elif "昨天" in raw:
        d = (today - timedelta(days=1)).isoformat()
    elif "今天" in raw:
        d = today.isoformat()
    else:
        md = _re.search(r"(\d{1,2})\s*月\s*(\d{1,2})", raw)
        if md:
            mo, dd = int(md.group(1)), int(md.group(2))
            try:
                d = date(today.year, mo, dd).isoformat()
            except ValueError:
                pass
    return pet_id, pet_name, d


def parse_diet_text(text: str) -> dict:
    """「今天两顿干粮」→ 饮食草稿。"""
    import re as _re
    raw = (text or "").strip()
    pet_id, pet_name, d = _parse_pet_and_date(raw)
    food = "other"
    for k, kws in (("kibble", ("干粮", "猫粮", "狗粮", "主粮", "粮")),
                   ("wet", ("湿粮", "罐头", "罐罐")),
                   ("treat", ("零食", "猫条", "冻干", "肉条")),
                   ("raw", ("生骨肉", "生骨"))):
        if any(w in raw for w in kws):
            food = k
            break
    amount = raw
    if pet_name:
        amount = amount.replace(pet_name, " ")
    m = _re.search(r"(\d+(?:\.\d+)?)\s*(顿|次|餐|粒|块|袋|罐|包|g|克|ml)", raw)
    amt = f"{m.group(1)} {m.group(2)}" if m else ""
    note = _re.sub(r"\s+", " ", raw.replace(pet_name or "", " ")).strip()[:200]
    out = {"pet_id": pet_id, "pet_name": pet_name, "food_type": food,
           "amount": amt or note or "1 顿", "date": d, "note": note if amt else "",
           "hints": []}
    if not amt:
        out["hints"].append("amount")
    return out


def parse_med_text(text: str) -> dict:
    """「可乐耳药一天两次」→ 用药草稿。"""
    import re as _re
    raw = (text or "").strip()
    pet_id, pet_name, d = _parse_pet_and_date(raw)
    freq = ""
    if "一天三次" in raw or "每日三次" in raw or "一日三次" in raw:
        freq = "每日三次"
    elif "一天两次" in raw or "每日两次" in raw or "一日两次" in raw or "早晚" in raw:
        freq = "每日两次"
    elif "一天一次" in raw or "每日一次" in raw or "一日一次" in raw:
        freq = "每日一次"
    elif "隔天" in raw:
        freq = "隔天一次"
    elif "每周" in raw:
        freq = "每周一次"
    elif "按需" in raw:
        freq = "按需"
    dosage = ""
    dm = _re.search(r"(\d+(?:\.\d+)?)\s*(片|粒|滴|袋|ml|毫升|泵)", raw)
    if dm:
        dosage = f"{dm.group(1)}{dm.group(2)}"
    name = _re.sub(r"\s+", " ", raw)
    for drop in (pet_name or "", "今天", "昨天", "开始吃", "继续吃", "吃", "用", "滴", "涂"):
        name = name.replace(drop, " ")
    name = _re.sub(r"一天[一二三]次|每日[一二三]次|一日[一二三]次|隔天一次|每周一次|按需", " ", name)
    name = _re.sub(r"\s+", " ", name).strip(" ，,、的")
    if not name:
        name = "用药"
    out = {"pet_id": pet_id, "pet_name": pet_name, "name": name[:40],
           "dosage": dosage or "1 次量", "frequency": freq or "每日一次",
           "start_date": d, "note": raw[:200], "hints": []}
    if not freq:
        out["hints"].append("frequency")
    return out


def parse_weight_text(text: str) -> dict:
    """「可乐称了 11.2 公斤」→ 体重草稿。"""
    import re as _re
    raw = (text or "").strip()
    pet_id, pet_name, d = _parse_pet_and_date(raw)
    w = None
    m = _re.search(r"(\d+(?:\.\d+)?)\s*(?:公斤|kg|KG|Kg|斤|千克)?", raw)
    if m:
        w = float(m.group(1))
        if "斤" in raw:
            w = round(w / 2, 2)
    if w is not None and not (0 < w <= 500):
        w = None
    out = {"pet_id": pet_id, "pet_name": pet_name,
           "weight": w, "date": d, "note": raw[:100],
           "hints": ([] if w is not None else ["weight"]) + ([] if pet_id else ["pet"])}
    return out


def create_record_draft(pet_name: str, record_type: str, date: str, title: str,
                        note: str = "", next_date: str = "", weight: float | None = None) -> str:
    """根据用户口语描述起草一条健康记录（不直接入库，待用户在前端确认后保存）。
    相对日期（昨天/下周四等）应先按今天换算为 YYYY-MM-DD。"""
    import species
    pet = db.fetch_pet_by_name(pet_name)
    if not pet:
        return _pet_missing_text(pet_name)
    rtype = (record_type or "").strip().lower()
    if rtype not in db.RECORD_TYPES:
        rev = {v: k for k, v in db.RECORD_TYPES.items()}
        rtype = rev.get((record_type or "").strip(), "")
        if not rtype:
            return (f"记录类型「{record_type}」无效。可用类型："
                    + "、".join(db.RECORD_TYPES.values()) + "。")
    ok, msg = species.record_type_allowed(pet["type"], rtype)
    if not ok:
        return msg + "。请向用户说明该物种不适用此记录类型，不要起草。"
    title = (title or "").strip()
    if not title:
        return "缺少记录标题。请先向用户询问具体事项名称，再重新起草。"
    draft = {
        "pet_id": pet["id"], "pet_name": pet["name"],
        "type": rtype, "type_label": db.RECORD_TYPES[rtype],
        "date": (date or "").strip() or db.today_str(),
        "title": title,
        "note": (note or "").strip(),
        "next_date": (next_date or "").strip() or None,
        "weight": weight,
    }
    line = json.dumps(draft, ensure_ascii=False)
    global LAST_DRAFT
    LAST_DRAFT = draft
    summary = (f"{draft['pet_name']} · {draft['type_label']} · {draft['date']} · {draft['title']}"
               + (f" · 下次 {draft['next_date']}" if draft["next_date"] else ""))
    return (f"已起草记录（等待用户确认）：{summary}\n"
            f"{DRAFT_MARKER}{line}@@END@@\n"
            "请用一两句话告诉用户草稿已准备好、请在下方卡片中确认或取消；"
            "标记行是给系统的，不要在回答中原样输出。")


def get_attention_ranking() -> str:
    """多宠物关注优先级：综合逾期、临期、体重波动与记录陈旧度评分排序，评分越高越紧急。无需参数。"""
    pets = db.list_pets()
    if not pets:
        return "系统中暂无宠物。"
    scored = []
    for p in pets:
        score, reasons = 0, []
        overdue = [r for r in (p["upcoming"] or []) if r["days_left"] < 0]
        due = [r for r in (p["upcoming"] or []) if 0 <= r["days_left"] <= 7]
        if overdue:
            score += len(overdue) * 3
            reasons.append(f"{len(overdue)} 项逾期（{ '、'.join(r['title'] for r in overdue[:2]) }）")
        if due:
            score += len(due) * 2
            reasons.append(f"{len(due)} 项临期")
        weights = db.list_weight_logs(p["id"])
        if len(weights) >= 2 and weights[0]["weight"]:
            delta = abs(weights[-1]["weight"] - weights[0]["weight"])
            if delta / weights[0]["weight"] >= 0.1:
                score += 2
                reasons.append(f"体重波动 {round(delta, 2)}kg 超 10%")
        if p["record_count"] == 0:
            score += 3
            reasons.append("尚无任何健康记录")
        else:
            records = db.list_records(p["id"])
            if records:
                gap = db.days_until(records[0]["date"])
                if -gap > 60:
                    score += 2
                    reasons.append(f"已 {-gap} 天无新记录")
        if score > 0:
            scored.append((score, p, reasons))
    if not scored:
        return "所有宠物状态都在计划内：无逾期、无临期、体重平稳、记录新鲜，暂时不需要特别关注谁。"
    scored.sort(key=lambda x: -x[0])
    lines = ["多宠物关注优先级（评分越高越紧急）："]
    for i, (score, p, reasons) in enumerate(scored, 1):
        lines.append(f"{i}. {p['name']}（{db.PET_TYPES.get(p['type'], p['type'])}）—— 评分 {score}："
                     + "；".join(reasons))
    return "\n".join(lines)


def query_expenses(name: str = "", month: str = "") -> str:
    """查询养宠花费：合计金额、分类占比、按宠物分摊与最近明细。name 留空表示全部宠物（含家庭共同支出）；
    month 传 YYYY-MM 查某月、传 YYYY 查全年、留空查本月。只读，不做任何写入。"""
    from datetime import date
    name = (name or "").strip()
    pet = None
    if name:
        pet = db.fetch_pet_by_name(name)
        if not pet:
            return _pet_missing_text(name)
    m = (month or "").strip()
    today = date.today()
    year, mon, scope = today.year, today.month, "本月"
    if m:
        parts = m.replace("/", "-").split("-")
        try:
            if len(parts) == 1 and len(parts[0]) == 4 and parts[0].isdigit():
                year, mon, scope = int(parts[0]), None, f"{parts[0]} 年全年"
            elif len(parts) >= 2:
                year, mon = int(parts[0]), int(parts[1])
                scope = f"{year} 年 {mon} 月"
                if not 1 <= mon <= 12:
                    raise ValueError
            else:
                raise ValueError
        except ValueError:
            return f"月份「{month}」无法识别，请用 YYYY-MM（如 2026-09）或 YYYY（如 2026）。"
    pid = pet["id"] if pet else None
    summ = db.expense_summary(year, mon, pid)
    rows = db.list_expenses(year, mon, pid, limit=8)
    who = f"「{pet['name']}」" if pet else "全部宠物（含家庭共同支出）"
    if not summ["count"]:
        return f"{who}{scope}没有任何花费记录。"
    lines = [f"{who}{scope}花费合计 ¥{summ['total']:.2f}，共 {summ['count']} 笔。"]
    cats = sorted(summ["by_category"].items(), key=lambda kv: -kv[1])
    if cats:
        top = "；".join(f"{db.EXPENSE_CATEGORIES.get(k, k)} ¥{v:.2f}"
                       f"（{round(v / summ['total'] * 100) if summ['total'] else 0}%）" for k, v in cats)
        lines.append(f"- 分类：{top}")
        lines.append(f"- 最大开销类别：{db.EXPENSE_CATEGORIES.get(cats[0][0], cats[0][0])}")
    if not pet and len(summ["by_pet"]) > 1:
        lines.append("- 按宠物：" + "；".join(f"{r['pet_name']} ¥{r['total']:.2f}" for r in summ["by_pet"]))
    lines.append("- 最近明细：")
    for r in rows:
        owner = r["pet_name"] or "家庭共同"
        lines.append(f"  - {r['date']} {owner}【{r['category_label']}】¥{r['amount']:.2f}"
                     + (f" {r['note']}" if r.get("note") else ""))
    return "\n".join(lines)


def query_feeding(name: str) -> str:
    """查询某只宠物的饮食日志：今日/本周喂食次数、近 30 天饮食类型分布，以及最近 10 条流水（吃什么、吃多少、备注）。只读。"""
    pet = db.fetch_pet_by_name(name)
    if not pet:
        return _pet_missing_text(name)
    logs = db.list_feeding_logs(pet["id"], limit=10)
    if not logs:
        return f"「{pet['name']}」还没有饮食记录，可以在档案的「饮食」页签记下第一顿。"
    s = db.feeding_summary(pet["id"])
    lines = [f"「{pet['name']}」饮食情况：今日 {s['today']} 顿，本周 {s['week']} 次。"]
    if s["by_type_30d"]:
        lines.append("- 近 30 天类型：" + "、".join(
            f"{db.FEEDING_TYPES.get(k, k)}{v}次" for k, v in sorted(s["by_type_30d"].items(), key=lambda kv: -kv[1])))
    lines.append("- 最近记录：")
    for l in logs:
        lines.append(f"  - {l['date']}【{l['type_label']}】{l['amount']}" + (f"：{l['note']}" if l.get("note") else ""))
    return "\n".join(lines)


def query_medbox(name: str = "") -> str:
    """查询家庭药箱库存：药品名/剂型/数量/有效期与开封后剩余天数、状态（可用/临期/已过期）与关联宠物。
    name 传宠物名时只看它相关的药品；留空查全部。只读，不做任何写入。"""
    name = (name or "").strip()
    pet = None
    if name:
        pet = db.fetch_pet_by_name(name)
        if not pet:
            return _pet_missing_text(name)
    items = db.list_medbox()
    if pet:
        items = [i for i in items if any(p["id"] == pet["id"] for p in i["pets"])]
    if not items:
        return (f"「{pet['name']}」还没有关联任何药箱药品，可以在「药箱」页面录入。" if pet
                else "药箱还是空的，可以在「药箱」页面录入第一件常备药。")
    expired = sum(1 for i in items if i["status"] == "expired")
    soon = sum(1 for i in items if i["status"] == "soon")
    who = f"「{pet['name']}」相关药品" if pet else "家庭药箱"
    lines = [f"{who}共 {len(items)} 种｜过期 {expired} 临期 {soon}。"]
    for i in items:
        qty = ""
        if i.get("qty") is not None:
            q = int(i["qty"]) if float(i["qty"]).is_integer() else i["qty"]
            qty = f"，{q}{i.get('unit') or ''}"
        line = f"- {i['name']}（{i['form_label']}{qty}）"
        if i["remain_days"] is None:
            line += f"，{i['effective_deadline']} 到期" if i["effective_deadline"] else "，无期限"
        elif i["remain_days"] < 0:
            line += f"，已过期 {-i['remain_days']} 天（截止 {i['effective_deadline']}）"
        else:
            line += f"，{i['status_label']}（剩 {i['remain_days']} 天，截止 {i['effective_deadline']}）"
        if i["opened"]:
            line += "，已开封"
        if i.get("purpose"):
            line += f"，用途：{i['purpose']}"
        if i["pets"]:
            line += "，适用：" + "、".join(p["name"] for p in i["pets"])
        if i.get("location"):
            line += f"，放在{i['location']}"
        lines.append(line)
    if expired:
        lines.append("- ⚠️ 有过期药品，请清理并按需补购；是否换药补药以兽医意见为准。")
    return "\n".join(lines)


# 供 agent.py 注册 LangChain 工具用的元信息
TOOL_META = [
    ("query_pet", "按宠物名字查询该宠物的基本信息（品种/年龄/体重/健康状态）"),
    ("query_health_records", "按宠物名字查询其健康记录（疫苗/体检/驱虫/喂药/就诊），按时间倒序"),
    ("get_reminders", "查询所有临期（7天内）或已逾期的健康事项，无需参数"),
    ("analyze_health", "按宠物名字做健康分析：记录概况、提醒、体重变化与结论"),
    ("generate_report", "生成健康报告（Markdown）。参数：宠物名（可为空表示全部）、周期（周/月/年）"),
    ("query_memories", "查询宠物回忆故事/成长记录。参数：宠物名（可为空表示全部）"),
    ("get_care_guide", "查询物种护理规范：该物种适用的记录类型、该做与不该做的事、常见疾病。参数：宠物名或类型词（可为空表示概览）"),
    ("create_record_draft", "根据用户口语描述起草健康记录草稿（不入库，用户确认后保存）。参数：宠物名、类型、日期、标题、说明、下次日期、体重"),
    ("get_attention_ranking", "多宠物关注优先级排序：综合逾期、临期、体重波动、记录陈旧度评分，回答'该先管哪只'类问题。无需参数"),
    ("query_medications", "按宠物名字查询用药情况：在用药物的剂量/频次/疗程剩余天数，以及已结束的用药历史"),
    ("query_expenses", "查询养宠花费：合计/分类占比/按宠物分摊/最近明细。参数：宠物名（可为空表示全部）、月份 YYYY-MM 或 YYYY（可为空表示本月）"),
    ("query_feeding", "按宠物名字查询饮食日志：今日/本周喂食次数、近 30 天类型分布与最近流水"),
    ("query_medbox", "查询家庭药箱库存：药品/剂型/数量/有效期与开封后剩余天数、过期临期状态与关联宠物。参数：宠物名（可为空表示全部）"),
]
