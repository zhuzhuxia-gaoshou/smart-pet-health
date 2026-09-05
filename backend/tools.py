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
        lines.append(line)
    return "\n".join(lines)


def get_reminders(_input: str = "") -> str:
    """列出所有临期（≤7天）或已逾期的事项。无需参数。"""
    rem = db.compute_reminders()
    if not rem:
        return "当前没有临期或逾期的事项，一切都在计划内。"
    lines = [f"共 {len(rem)} 项需要关注："]
    for r in rem:
        if r["overdue"]:
            lines.append(f"- ⚠️ 已逾期 {-r['days_left']} 天：{r['pet_name']} 的"
                         f"【{r['type_label']}】{r['title']}（应于 {r['next_date']}）")
        else:
            lines.append(f"- ⏰ 还剩 {r['days_left']} 天：{r['pet_name']} 的"
                         f"【{r['type_label']}】{r['title']}（下次日期 {r['next_date']}）")
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


def analyze_health(name: str) -> str:
    """结合健康记录与体重历史，对某只宠物做简要健康分析。"""
    pet = db.fetch_pet_by_name(name)
    if not pet:
        return _pet_missing_text(name)
    records = db.list_records(pet["id"])
    weights = db.list_weight_logs(pet["id"])
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


# 供 agent.py 注册 LangChain 工具用的元信息
TOOL_META = [
    ("query_pet", "按宠物名字查询该宠物的基本信息（品种/年龄/体重/健康状态）"),
    ("query_health_records", "按宠物名字查询其健康记录（疫苗/体检/驱虫/喂药/就诊），按时间倒序"),
    ("get_reminders", "查询所有临期（7天内）或已逾期的健康事项，无需参数"),
    ("analyze_health", "按宠物名字做健康分析：记录概况、提醒、体重变化与结论"),
    ("generate_report", "生成健康报告（Markdown）。参数：宠物名（可为空表示全部）、周期（周/月/年）"),
    ("query_memories", "查询宠物回忆故事/成长记录。参数：宠物名（可为空表示全部）"),
    ("get_care_guide", "查询物种护理规范：该物种适用的记录类型、该做与不该做的事、常见疾病。参数：宠物名或类型词（可为空表示概览）"),
]
