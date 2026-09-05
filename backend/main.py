# -*- coding: utf-8 -*-
"""main.py — FastAPI 应用：REST API + 前端静态托管。

启动：python main.py  →  http://127.0.0.1:8000
"""
import json
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import db
import species

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_and_seed()
    yield


app = FastAPI(title="智能宠物健康管家", version="2.1", lifespan=lifespan)

# 开发期放开 CORS（允许前端跨端口调试）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- 模型

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


class RecordIn(BaseModel):
    type: str = Field(..., description="vaccine|checkup|deworm|medication|clinic")
    date: str | None = None
    title: str = Field(..., min_length=1, max_length=60)
    note: str | None = None
    next_date: str | None = None
    weight: float | None = None


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    session_id: int | None = Field(None, description="会话 id；为空则新建会话")


class WeightIn(BaseModel):
    weight: float = Field(..., gt=0, description="体重 kg")
    date: str | None = None


class MemoryIn(BaseModel):
    date: str = Field(..., min_length=8, max_length=10)
    title: str = Field(..., min_length=1, max_length=60)
    pet_id: int | None = None
    text: str | None = Field(None, max_length=2000)
    image: str | None = None   # base64 data URI（前端已压缩）


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
    result = db.update_record(record_id, rec.model_dump(exclude_none=True))
    return {"record": result}


@app.delete("/api/records/{record_id}")
def api_delete_record(record_id: int):
    if not db.delete_record(record_id):
        return {"error": "记录不存在"}
    return {"ok": True}


# ---------------------------------------------------------------- 体重 / 提醒 / 统计

@app.get("/api/pets/{pet_id}/weights")
def api_list_weights(pet_id: int):
    return {"weights": db.list_weight_logs(pet_id)}


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


@app.get("/api/reminders")
def api_reminders():
    return {"reminders": db.compute_reminders()}


@app.get("/api/records")
def api_all_records(limit: int | None = None):
    return {"records": db.list_all_records(limit)}


@app.get("/api/stats")
def api_stats():
    return {"stats": db.stats(), "reminders": db.compute_reminders(),
            "recent": db.recent_activity()}


# ---------------------------------------------------------------- AI 今日简报

BRIEF_KEY = "briefing"


def _briefing_signature() -> str:
    """数据签名：宠物/记录/临期数任一变化即视为过期。"""
    st = db.stats()
    return f"{st['pet_count']}|{st['record_count']}|{st['due_count']}"


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
            result = {"reply": text, "mode": retry["mode"]}
    db.add_chat_message("user", body.message, sid)
    db.add_chat_message("assistant", result["reply"], sid, is_draft=bool(draft))
    db.touch_session(sid)
    result["session_id"] = sid
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
    """主应用单页（仪表盘/宠物/回忆集/AI 助手）。"""
    return FileResponse(os.path.join(WEB_DIR, "index.html"),
                        headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
