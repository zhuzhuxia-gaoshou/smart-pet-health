# -*- coding: utf-8 -*-
"""main.py — FastAPI 应用：REST API + 前端静态托管。

启动：python main.py  →  http://127.0.0.1:8000
"""
import os

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import db

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")

app = FastAPI(title="智能宠物健康管家", version="2.1")

# 开发期放开 CORS（允许前端跨端口调试）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init_and_seed()


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


@app.get("/api/stats")
def api_stats():
    return {"stats": db.stats(), "reminders": db.compute_reminders(),
            "recent": db.recent_activity()}


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
