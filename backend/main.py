# -*- coding: utf-8 -*-
"""main.py — FastAPI 应用：REST API + 前端静态托管。

启动：python main.py  →  http://127.0.0.1:8000
"""
import csv
import io
import json
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

import db
import species

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")

def _daily_backup_loop() -> None:
    """守护线程：每小时看一眼，当天还没有快照就备一份（跨天自动续备，随进程退出）。"""
    import time
    while True:
        time.sleep(3600)
        try:
            if not db.today_backup_done():
                db.backup_db()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_and_seed()
    try:
        db.backup_db()   # 启动即快照一份；失败不阻塞启动
    except Exception:
        pass
    threading.Thread(target=_daily_backup_loop, daemon=True).start()
    yield


app = FastAPI(title="智能宠物健康管家", version="2.2", lifespan=lifespan)

# 开发期放开 CORS（允许前端跨端口调试）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def _validation_error(_req: Request, exc: RequestValidationError):
    """参数校验失败统一为 {error} 形态（前端按 data.error 提示），而非默认的 detail 数组。"""
    msgs = []
    for e in exc.errors():
        loc = "·".join(str(x) for x in e.get("loc", []) if x not in ("body", "query", "path"))
        msg = str(e.get("msg", "")).replace("Value error, ", "")
        msgs.append(f"{loc}：{msg}" if loc else msg)
    return JSONResponse(status_code=422, content={"error": "；".join(msgs) or "请求参数无效"})


# ---------------------------------------------------------------- 模型

def _check_date(v: str | None) -> str | None:
    """日期字段统一校验：空值放过，否则必须是 YYYY-MM-DD。"""
    if v in (None, ""):
        return v
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise ValueError("日期格式应为 YYYY-MM-DD")
    return v


class PetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=30)
    type: str | None = None      # cat|dog|bird|other
    breed: str | None = None
    gender: str | None = None    # male|female|unknown
    birthday: str | None = None  # YYYY-MM-DD
    weight: float | None = None
    status: str | None = "healthy"  # healthy|attention|ill
    personality: str | None = None
    avatar: str | None = None

    @field_validator("birthday")
    @classmethod
    def _vd(cls, v):
        return _check_date(v)


class RecordIn(BaseModel):
    type: str = Field(..., description="vaccine|checkup|deworm|medication|clinic")
    date: str | None = None
    title: str = Field(..., min_length=1, max_length=60)
    note: str | None = None
    next_date: str | None = None
    weight: float | None = None
    repeat_rule: str | None = Field(None, description="''|daily|weekly|monthly|yearly；配合 next_date 使用")

    @field_validator("date", "next_date")
    @classmethod
    def _vd(cls, v):
        return _check_date(v)

    @field_validator("type")
    @classmethod
    def _vt(cls, v):
        if v not in db.RECORD_TYPES:
            raise ValueError("记录类型无效，可选：" + "/".join(db.RECORD_TYPES))
        return v


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    session_id: int | None = Field(None, description="会话 id；为空则新建会话")


class WeightIn(BaseModel):
    weight: float = Field(..., gt=0, description="体重 kg")
    date: str | None = None

    @field_validator("date")
    @classmethod
    def _vd(cls, v):
        return _check_date(v)


class MemoryIn(BaseModel):
    date: str = Field(..., min_length=8, max_length=10)
    title: str = Field(..., min_length=1, max_length=60)
    pet_id: int | None = None
    text: str | None = Field(None, max_length=2000)
    image: str | None = None   # base64 data URI（前端已压缩）

    @field_validator("date")
    @classmethod
    def _vd(cls, v):
        return _check_date(v)


class MedicationIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60, description="药名")
    dosage: str | None = Field(None, max_length=60, description="剂量，如：每侧 2 滴 / 1 袋")
    frequency: str | None = Field(None, max_length=40, description="频次，如：每日两次")
    start_date: str | None = None
    end_date: str | None = None
    status: str | None = Field(None, description="active|finished")
    note: str | None = Field(None, max_length=300)

    @field_validator("start_date", "end_date")
    @classmethod
    def _vd(cls, v):
        return _check_date(v)


class ExpenseIn(BaseModel):
    pet_id: int | None = Field(None, description="宠物 id；空或 0 表示家庭共同支出")
    date: str | None = None
    category: str = Field(..., description="medical|food|supply|grooming|other")
    amount: float = Field(..., description="金额（元），>0 且最多两位小数")
    note: str | None = Field(None, max_length=200)

    @field_validator("date")
    @classmethod
    def _vd(cls, v):
        return _check_date(v)

    @field_validator("category")
    @classmethod
    def _vc(cls, v):
        if v not in db.EXPENSE_CATEGORIES:
            raise ValueError("分类无效，可选：" + "/".join(db.EXPENSE_CATEGORIES))
        return v

    @field_validator("amount")
    @classmethod
    def _va(cls, v):
        # 中文校验替代 gt/le：NaN/inf 一并拦截；两位小数用整数化比较避开浮点误差
        if v is None or v != v or v in (float("inf"), float("-inf")):
            raise ValueError("金额无效")
        if v <= 0:
            raise ValueError("金额必须大于 0")
        if v > 1_000_000:
            raise ValueError("金额过大（上限 1,000,000）")
        if abs(v * 100 - round(v * 100)) > 1e-6:
            raise ValueError("金额最多保留两位小数")
        return round(v, 2)


class FeedingIn(BaseModel):
    date: str | None = None
    food_type: str = Field(..., description="kibble|wet|treat|raw|other")
    amount: str = Field(..., min_length=1, max_length=40, description="份量文本，如：80 g / 1 罐 / 半勺")
    note: str | None = Field(None, max_length=200)

    @field_validator("date")
    @classmethod
    def _vd(cls, v):
        return _check_date(v)

    @field_validator("food_type")
    @classmethod
    def _vt(cls, v):
        if v not in db.FEEDING_TYPES:
            raise ValueError("饮食类型无效，可选：" + "/".join(db.FEEDING_TYPES))
        return v

    @field_validator("amount")
    @classmethod
    def _va(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("份量不能为空")
        return v


# ---------------------------------------------------------------- 宠物 CRUD

@app.get("/api/pets")
def api_list_pets():
    return {"pets": db.list_pets()}


@app.post("/api/pets")
def api_add_pet(pet: PetIn):
    return {"pet": db.add_pet(pet.model_dump(exclude_none=True))}


@app.put("/api/pets/{pet_id}")
def api_update_pet(pet_id: int, pet: PetIn):
    result = db.update_pet(pet_id, pet.model_dump(exclude_none=True))
    if result is None:
        return {"error": "宠物不存在"}
    return {"pet": result}


@app.delete("/api/pets/{pet_id}")
def api_delete_pet(pet_id: int):
    if db.get_pet(pet_id) is None:
        return {"error": "宠物不存在"}
    db.delete_pet(pet_id)
    return {"ok": True}


@app.get("/api/pets/{pet_id}")
def api_get_pet(pet_id: int):
    pet = db.get_pet(pet_id)
    return {"pet": pet} if pet else {"error": "宠物不存在"}


# ---------------------------------------------------------------- 健康记录

@app.get("/api/pets/{pet_id}/records")
def api_list_records(pet_id: int):
    if db.get_pet(pet_id) is None:
        return {"error": "宠物不存在"}
    return {"records": db.list_records(pet_id)}


@app.post("/api/pets/{pet_id}/records")
def api_add_record(pet_id: int, rec: RecordIn):
    pet = db.get_pet(pet_id)
    if pet is None:
        return {"error": "宠物不存在"}
    # 物种校验：不适用的记录类型直接拒绝（如给鱼记疫苗）
    ok, msg = species.record_type_allowed(pet["type"], rec.type)
    if not ok:
        return {"error": msg}
    if rec.repeat_rule is not None and rec.repeat_rule not in db.REPEAT_RULES:
        return {"error": "重复周期无效"}
    result = db.add_record(pet_id, rec.model_dump(exclude_none=True))
    return {"record": result}


@app.put("/api/records/{record_id}")
def api_update_record(record_id: int, rec: RecordIn):
    old = db.get_record(record_id)
    if old is None:
        return {"error": "记录不存在"}
    pet = db.get_pet(old["pet_id"])
    ok, msg = species.record_type_allowed(pet["type"] if pet else None, rec.type)
    if not ok:
        return {"error": msg}
    if rec.repeat_rule is not None and rec.repeat_rule not in db.REPEAT_RULES:
        return {"error": "重复周期无效"}
    result = db.update_record(record_id, rec.model_dump(exclude_none=True))
    return {"record": result}


@app.post("/api/records/{record_id}/complete")
def api_complete_record(record_id: int):
    """标记提醒事项「本轮已做」：清空下次日期；有重复周期则自动生成下一轮记录。"""
    result = db.complete_record(record_id)
    if result is None:
        return {"error": "记录不存在"}
    return result


@app.delete("/api/records/{record_id}")
def api_delete_record(record_id: int):
    if not db.delete_record(record_id):
        return {"error": "记录不存在"}
    return {"ok": True}


# ---------------------------------------------------------------- 体重 / 提醒 / 统计

@app.get("/api/pets/{pet_id}/weights")
def api_list_weights(pet_id: int):
    return {"weights": db.list_weight_logs(pet_id)}


@app.get("/api/pets/{pet_id}/weight-insight")
def api_weight_insight(pet_id: int):
    """体重趋势 AI 解读（按数据签名缓存）。"""
    import agent
    return agent.weight_insight(pet_id)


@app.get("/api/care-plan/{pet_id}")
def api_care_plan(pet_id: int):
    """AI 月度护理计划：结构化计划项 + AI 总结，可逐项转为记录。"""
    import agent
    return agent.generate_care_plan(pet_id)


# ---------------------------------------------------------------- 数据导出

def _csv_response(filename: str, header: list, rows: list):
    """生成带 BOM 的 CSV（Excel 直接打开中文不乱码）。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    data = b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
    return StreamingResponse(iter([data]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.get("/api/export/pets.csv")
def api_export_pets():
    pets = db.list_pets()
    rows = [[p["id"], p["name"], db.PET_TYPES.get(p["type"], p["type"]), p.get("breed") or "",
             db.GENDERS.get(p.get("gender"), "未知"), p.get("birthday") or "", p.get("latest_weight") or "",
             db.PET_STATUS.get(p.get("status"), p.get("status")), p.get("personality") or "",
             p["record_count"], p.get("created_at") or ""] for p in pets]
    return _csv_response("pets.csv",
                         ["ID", "名字", "类型", "品种", "性别", "生日", "当前体重(kg)", "健康状态", "性格备注", "健康记录数", "创建时间"],
                         rows)


@app.get("/api/export/records.csv")
def api_export_records():
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d")
    rows = [[r["id"], r["pet_name"], r["type_label"], r["title"], r["date"],
             r.get("next_date") or "", r.get("note") or ""] for r in db.list_all_records()]
    return _csv_response(f"health-records-{stamp}.csv",
                         ["ID", "宠物", "类型", "标题", "日期", "下次日期", "说明"],
                         rows)


@app.get("/api/export/expenses.csv")
def api_export_expenses(year: int | None = None, month: int | None = None):
    """花费流水 CSV（可按年/月筛选，缺省全部），金额保留两位小数；文件名带筛选范围。"""
    err = _check_month(year, month)
    if err:
        return JSONResponse(status_code=422, content={"error": err})
    rows = [[e["id"], e["date"], e["pet_name"] or "家庭共同", e["category_label"],
             f"{e['amount']:.2f}", e.get("note") or ""] for e in db.list_expenses(year, month)]
    scope = f"{year}-{month:02d}" if (year and month) else (str(year) if year else "all")
    return _csv_response(f"expenses-{scope}.csv",
                         ["ID", "日期", "宠物", "分类", "金额(元)", "备注"], rows)


@app.get("/api/export/db")
def api_export_db():
    """下载数据库文件备份。"""
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return FileResponse(db.DB_PATH, filename=f"petcare-backup-{stamp}.db",
                        headers={"Cache-Control": "no-cache"})


@app.post("/api/admin/reset-demo")
def api_reset_demo():
    """清空全部数据并重建演示示例（危险操作，前端已有二次确认）。"""
    return db.reset_demo_data()


@app.post("/api/pets/{pet_id}/weights")
def api_add_weight(pet_id: int, body: WeightIn):
    result = db.add_weight_log(pet_id, body.model_dump(exclude_none=True))
    if result is None:
        return {"error": "宠物不存在"}
    return {"weight_log": result}


@app.get("/api/species")
def api_species():
    """物种档案：各类型适用的记录类型、常见疾病、该做与不该做的事。"""
    return {"species": species.api_payload()}


# ---------------------------------------------------------------- 用药记录

@app.get("/api/pets/{pet_id}/medications")
def api_list_medications(pet_id: int):
    if db.get_pet(pet_id) is None:
        return {"error": "宠物不存在"}
    return {"medications": db.list_medications(pet_id)}


@app.post("/api/pets/{pet_id}/medications")
def api_add_medication(pet_id: int, med: MedicationIn):
    if med.status is not None and med.status not in db.MED_STATUS:
        return {"error": "用药状态无效"}
    result = db.add_medication(pet_id, med.model_dump(exclude_none=True))
    if result is None:
        return {"error": "宠物不存在"}
    return {"medication": result}


@app.put("/api/medications/{med_id}")
def api_update_medication(med_id: int, med: MedicationIn):
    if med.status is not None and med.status not in db.MED_STATUS:
        return {"error": "用药状态无效"}
    # 显式允许把 end_date 清空：前端传空串表示"无结束日期"
    data = med.model_dump(exclude_none=True)
    result = db.update_medication(med_id, data)
    if result is None:
        return {"error": "用药记录不存在"}
    return {"medication": result}


@app.delete("/api/medications/{med_id}")
def api_delete_medication(med_id: int):
    if not db.delete_medication(med_id):
        return {"error": "用药记录不存在"}
    return {"ok": True}


# ---------------------------------------------------------------- 花费记账

def _check_month(year: int | None, month: int | None) -> str | None:
    if month is not None and not 1 <= month <= 12:
        return "月份应在 1~12 之间"
    if year is not None and not 2000 <= year <= 2100:
        return "年份超出范围"
    return None


@app.get("/api/expenses")
def api_list_expenses(year: int | None = None, month: int | None = None,
                      pet_id: int | None = None, category: str | None = None):
    """花费流水 + 聚合（合计/分类合计/按宠物合计）；year/month/pet_id/category 可组合筛选。"""
    err = _check_month(year, month)
    if err:
        return JSONResponse(status_code=422, content={"error": err})
    return {"expenses": db.list_expenses(year, month, pet_id, category),
            "summary": db.expense_summary(year, month, pet_id, category)}


@app.post("/api/expenses")
def api_add_expense(body: ExpenseIn):
    result = db.add_expense(body.model_dump(exclude_none=True))
    if result is None:
        return {"error": "宠物不存在"}
    return {"expense": result}


# 月度预算：必须注册在 /api/expenses/{exp_id} 之前，否则 PUT budget 会被 int 路径参数抢走并 422
BUDGET_KEY = "expense_budget"


class BudgetIn(BaseModel):
    budget: float | None = Field(None, description="月度预算（元）；null 或 0 表示清除")

    @field_validator("budget")
    @classmethod
    def _vb(cls, v):
        if v is None:
            return None
        if v != v or v in (float("inf"), float("-inf")) or v < 0:
            raise ValueError("预算必须是不小于 0 的数字")
        if v > 10_000_000:
            raise ValueError("预算过大")
        return round(v, 2)


@app.get("/api/expenses/budget")
def api_get_budget():
    raw = db.kv_get(BUDGET_KEY)
    try:
        budget = round(float(raw), 2) if raw else None
    except ValueError:
        budget = None
    return {"budget": budget or None}


@app.put("/api/expenses/budget")
def api_set_budget(body: BudgetIn):
    """设置/清除月度预算（存 app_kv，全家庭共用一个数）。"""
    db.kv_set(BUDGET_KEY, str(body.budget) if body.budget else "")
    return {"budget": body.budget or None}


@app.get("/api/pets/{pet_id}/expenses")
def api_pet_expenses(pet_id: int, year: int | None = None, month: int | None = None):
    if db.get_pet(pet_id) is None:
        return {"error": "宠物不存在"}
    err = _check_month(year, month)
    if err:
        return JSONResponse(status_code=422, content={"error": err})
    return {"expenses": db.list_expenses(year, month, pet_id),
            "summary": db.expense_summary(year, month, pet_id)}


@app.post("/api/pets/{pet_id}/expenses")
def api_add_pet_expense(pet_id: int, body: ExpenseIn):
    if db.get_pet(pet_id) is None:
        return {"error": "宠物不存在"}
    data = body.model_dump(exclude_none=True)
    data["pet_id"] = pet_id
    result = db.add_expense(data)
    if result is None:
        return {"error": "宠物不存在"}
    return {"expense": result}


@app.put("/api/expenses/{exp_id}")
def api_update_expense(exp_id: int, body: ExpenseIn):
    if db.get_expense(exp_id) is None:
        return {"error": "花费记录不存在"}
    result = db.update_expense(exp_id, body.model_dump(exclude_none=True))
    if result is None:
        return {"error": "宠物不存在"}
    return {"expense": result}


@app.delete("/api/expenses/{exp_id}")
def api_delete_expense(exp_id: int):
    if not db.delete_expense(exp_id):
        return {"error": "花费记录不存在"}
    return {"ok": True}


# ---------------------------------------------------------------- 饮食日志

@app.get("/api/pets/{pet_id}/diet-logs")
def api_list_diet(pet_id: int):
    if db.get_pet(pet_id) is None:
        return {"error": "宠物不存在"}
    return {"logs": db.list_feeding_logs(pet_id), "summary": db.feeding_summary(pet_id)}


@app.post("/api/pets/{pet_id}/diet-logs")
def api_add_diet(pet_id: int, body: FeedingIn):
    result = db.add_feeding_log(pet_id, body.model_dump(exclude_none=True))
    if result is None:
        return {"error": "宠物不存在"}
    return {"log": result}


@app.put("/api/diet-logs/{log_id}")
def api_update_diet(log_id: int, body: FeedingIn):
    result = db.update_feeding_log(log_id, body.model_dump(exclude_none=True))
    if result is None:
        return {"error": "饮食记录不存在"}
    return {"log": result}


@app.delete("/api/diet-logs/{log_id}")
def api_delete_diet(log_id: int):
    if not db.delete_feeding_log(log_id):
        return {"error": "饮食记录不存在"}
    return {"ok": True}


# ---------------------------------------------------------------- 健康日历

@app.get("/api/calendar")
def api_calendar(year: int | None = None, month: int | None = None):
    """某月健康事项聚合：提醒（逾期/临期/待办分级）+ 已做记录 + 进行中用药，按日期分桶；缺省为本月。"""
    today = datetime.now()
    year, month = year or today.year, month or today.month
    err = _check_month(year, month)
    if err:
        return JSONResponse(status_code=422, content={"error": err})
    return db.calendar_month(year, month)


@app.get("/api/reminders")
def api_reminders():
    return {"reminders": db.compute_reminders()}


@app.get("/api/records")
def api_all_records(limit: int | None = None, pet_id: int | None = None,
                    type: str | None = None, offset: int = 0):
    """全部健康记录；支持 pet_id / type 筛选与 limit+offset 分页，total 为筛选后的总数。"""
    return {"records": db.list_all_records(limit, pet_id, type, offset),
            "total": db.count_records(pet_id, type)}


@app.get("/api/stats")
def api_stats():
    return {"stats": db.stats(), "reminders": db.compute_reminders(),
            "recent": db.recent_activity()}


# ---------------------------------------------------------------- AI 今日简报

BRIEF_KEY = "briefing"


def _briefing_signature() -> str:
    """数据签名：宠物/记录/临期数/本月花费笔数任一变化即视为过期。"""
    st = db.stats()
    now = datetime.now()
    exp_n = db.expense_summary(now.year, now.month)["count"]
    return f"{st['pet_count']}|{st['record_count']}|{st['due_count']}|{exp_n}"


def _start_briefing_generation() -> None:
    """后台线程生成简报并写缓存（不阻塞请求）。"""
    def work():
        try:
            import agent
            payload = agent.generate_briefing()
            payload["sig"] = _briefing_signature()
            payload["date"] = datetime.now().strftime("%Y-%m-%d")
            db.kv_set(BRIEF_KEY, json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()


@app.get("/api/briefing")
def api_briefing():
    """今日 AI 健康简报：按天+数据签名缓存；过期先返回旧内容并后台刷新。"""
    meta = db.kv_get_meta(BRIEF_KEY)
    sig = _briefing_signature()
    today = datetime.now().strftime("%Y-%m-%d")
    if meta:
        try:
            data = json.loads(meta["value"])
        except Exception:
            data = None
        if data and data.get("text"):
            fresh = data.get("sig") == sig and data.get("date") == today
            if not fresh:
                _start_briefing_generation()
            return {"briefing": {"text": data["text"], "mode": data.get("mode"),
                                 "generated_at": meta["updated_at"]},
                    "fresh": fresh}
    _start_briefing_generation()
    return {"briefing": None, "fresh": False}


@app.post("/api/briefing/refresh")
def api_briefing_refresh():
    """手动触发重新生成。"""
    _start_briefing_generation()
    return {"generating": True}


# ---------------------------------------------------------------- 回忆集

@app.get("/api/memories")
def api_list_memories(pet_id: int | None = None):
    return {"memories": db.list_memories(pet_id)}


@app.post("/api/memories")
def api_add_memory(mem: MemoryIn):
    result = db.add_memory(mem.model_dump(exclude_none=True))
    if result is None:
        return {"error": "宠物不存在"}
    return {"memory": result}


@app.put("/api/memories/{mem_id}")
def api_update_memory(mem_id: int, mem: MemoryIn):
    result = db.update_memory(mem_id, mem.model_dump(exclude_none=True))
    if result is None:
        return {"error": "回忆不存在或宠物不存在"}
    return {"memory": result}


@app.delete("/api/memories/{mem_id}")
def api_delete_memory(mem_id: int):
    if not db.delete_memory(mem_id):
        return {"error": "回忆不存在"}
    return {"ok": True}


# ---------------------------------------------------------------- AI 对话（P2 接入 Agent）

DRAFT_RE = re.compile(r"@@DRAFT@@(\{.*?\})@@END@@", re.S)
# 建档意图：用户口述"记一笔/记一下/打完疫苗"等，用于草稿兜底重试
DRAFT_INTENT_RE = re.compile(r"记一笔|记一下|记录一下|帮我记|帮我登记|登记一下|补充一条|添加一条记录")


@app.post("/api/chat")
def api_chat(body: ChatIn):
    import agent
    import tools
    # 会话：无 id 则以首条消息为题新建
    sid = body.session_id
    if sid is None or db.get_session(sid) is None:
        sid = db.create_session(title=body.message.strip()[:20])
    # 记忆：当前会话最近多轮（草稿消息以占位符注入，防止模型模仿格式）
    history = db.chat_history(sid, 6)
    result = agent.answer(body.message, history=history)
    # 提取 AI 起草的记录草稿（若有）：优先取工具暂存，标记行兜底
    draft = tools.take_last_draft()
    m = DRAFT_RE.search(result["reply"])
    if m:
        if draft is None:
            try:
                draft = json.loads(m.group(1))
            except Exception:
                draft = None
        result["reply"] = DRAFT_RE.sub("", result["reply"]).strip()
    # 兜底：检测到建档意图但模型没调起草工具（偶发模仿历史格式）→ 明确指令重试一次
    if draft is None and DRAFT_INTENT_RE.search(body.message):
        retry = agent.answer("请立即调用 create_record_draft 工具，为以下需求起草记录（不要只用文字描述）："
                             + body.message)
        d2 = tools.take_last_draft()
        if d2:
            draft = d2
            m2 = DRAFT_RE.search(retry["reply"])
            text = DRAFT_RE.sub("", retry["reply"]).strip() if m2 else retry["reply"]
            result = {"reply": text, "mode": retry["mode"], "expert": retry.get("expert"),
                      "expert_label": retry.get("expert_label"), "route": retry.get("route")}
    db.add_chat_message("user", body.message, sid)
    db.add_chat_message("assistant", result["reply"], sid, is_draft=bool(draft))
    db.touch_session(sid)
    result["session_id"] = sid
    result["provider"] = agent.current_provider()
    if draft:
        result["draft"] = draft
    return result


@app.get("/api/chat/sessions")
def api_chat_sessions():
    """历史会话列表（最近更新倒序）。"""
    return {"sessions": db.list_sessions()}


@app.get("/api/chat/history")
def api_chat_history(session_id: int):
    """某会话的历史消息（用于界面回看）。"""
    if db.get_session(session_id) is None:
        return {"error": "会话不存在"}
    return {"session_id": session_id, "messages": db.session_messages(session_id)}


@app.delete("/api/chat/sessions/{session_id}")
def api_delete_session(session_id: int):
    """删除一个历史会话及其全部消息。"""
    if not db.delete_session(session_id):
        return {"error": "会话不存在"}
    return {"ok": True}


@app.put("/api/chat/sessions/{session_id}")
def api_rename_session(session_id: int, body: ChatIn):
    """重命名历史会话（复用 ChatIn 的 message 字段作为新标题）。"""
    title = body.message.strip()[:30]
    if not title:
        return {"error": "标题不能为空"}
    if not db.rename_session(session_id, title):
        return {"error": "会话不存在"}
    return {"ok": True, "title": title}


@app.get("/api/agent/status")
def api_agent_status():
    import agent
    return agent.status()


# ---------------------------------------------------------------- 前端页面

@app.get("/")
def welcome():
    """网站入口：欢迎页 → 点「开始使用」→ 过渡动画 → 进入应用。"""
    return FileResponse(os.path.join(WEB_DIR, "welcome.html"),
                        headers={"Cache-Control": "no-cache"})


@app.get("/app")
def index():
    """主应用单页（仪表盘/宠物/记账/日历/回忆集/AI 助手）。"""
    return FileResponse(os.path.join(WEB_DIR, "index.html"),
                        headers={"Cache-Control": "no-cache"})


@app.get("/manifest.json")
def web_manifest():
    """PWA 清单（可安装；刻意不做 Service Worker，避免缓存干扰开发）。"""
    return FileResponse(os.path.join(WEB_DIR, "manifest.json"),
                        media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})


@app.get("/icon.svg")
def web_icon():
    """应用图标（爪印 SVG，manifest 与浏览器标签共用）。"""
    return FileResponse(os.path.join(WEB_DIR, "icon.svg"), media_type="image/svg+xml")


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
