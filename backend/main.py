# -*- coding: utf-8 -*-
"""main.py — FastAPI 应用：REST API + 前端静态托管。

启动：python main.py  →  http://127.0.0.1:8000
"""
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import db

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
    result = db.add_record(pet_id, rec.model_dump(exclude_none=True))
    if result is None:
        return {"error": "宠物不存在"}
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

@app.post("/api/chat")
def api_chat(body: ChatIn):
    import agent
    return agent.answer(body.message)


@app.get("/api/agent/status")
def api_agent_status():
    import agent
    return agent.status()


# ---------------------------------------------------------------- 前端页面

@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
