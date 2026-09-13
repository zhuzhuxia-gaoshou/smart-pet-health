# -*- coding: utf-8 -*-
"""agent.py — LangChain Agent（DeepSeek 工具调用 / 通义千问 ReAct）+ 无 Key 降级"示例回答"模式。

- 配置 DEEPSEEK_API_KEY   → DeepSeek Chat（OpenAI 兼容端点，原生 function calling 驱动 ReAct 循环）
- 仅配置 DASHSCOPE_API_KEY → 通义千问 ChatTongyi（若不支持工具调用则自动降级）
- 都未配置                → ExampleAgent（解析关键词，调用同一套工具查数据库拼回答）

对外入口：answer(message) -> {"reply": str, "mode": "agent"|"example"}
"""
import json
import os

import tools

_env_loaded = False


def _load_env() -> None:
    global _env_loaded
    if _env_loaded:
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    except ImportError:
        pass
    _env_loaded = True


def _key(name: str) -> str:
    _load_env()
    v = os.environ.get(name, "").strip()
    return "" if v.startswith("sk-xxx") else v


def deepseek_key() -> str:
    return _key("DEEPSEEK_API_KEY")


def dashscope_key() -> str:
    return _key("DASHSCOPE_API_KEY")


def api_key() -> str:
    """兼容旧接口：返回任一可用 Key（DeepSeek 优先）。"""
    return deepseek_key() or dashscope_key()


def provider() -> str | None:
    """当前 LLM 提供方：deepseek / tongyi / None。"""
    if deepseek_key():
        return "deepseek"
    if dashscope_key():
        return "tongyi"
    return None


# ---------------------------------------------------------------- Prompt

TOOLS_BRIEF = """可用工具：
- query_pet(宠物名): 查该宠物的基本信息（品种/年龄/体重/健康状态）
- query_health_records(宠物名): 查其健康记录（疫苗/体检/驱虫/喂药/就诊），按时间倒序
- get_reminders(): 查所有临期（7天内）或已逾期事项，无需参数
- analyze_health(宠物名): 综合记录与体重做健康分析
- generate_report(宠物名, 周期): 生成 Markdown 健康报告；名字留空表示全部宠物，周期取 周/月/年
- query_memories(宠物名): 查询主人为宠物手动写下的回忆故事（第一次郊游、纪念时刻等成长记录）；名字留空返回全部宠物的最新回忆
- get_care_guide(宠物名或类型词): 查询物种护理规范——该物种适用的记录类型、该做的事、不该做的事（禁忌）与常见疾病；名字留空返回概览
- create_record_draft(宠物名, 类型, 日期, 标题, 说明, 下次日期, 体重): 用户口述要记一笔健康事项时调用，起草待确认的记录草稿（不直接入库）"""

SYSTEM_PROMPT = """你是「智能宠物健康管家」的 AI 助手，一个专业的宠物健康管理 Agent。
你通过工具查询 SQLite 数据库中的真实宠物档案与健康记录，请遵循：
1. 回答宠物、疫苗、体检、驱虫、喂药、就诊、提醒、体重、报告相关问题前，必须先调用工具获取真实数据，严禁编造数据。
2. 需要多个信息时可以依次调用多个工具。
3. 用简体中文回答，语气专业、温暖、克制，适当使用 Markdown 排版。
4. 涉及医疗判断时，提醒用户以兽医意见为准。
5. 数据不足时诚实说明，并建议补充记录。
6. 生成报告时输出完整 Markdown 文本。
7. 物种边界：不同宠物生理差异极大。回答护理、疾病、饮食类问题前，先通过 query_pet 确认宠物类型，必要时调用 get_care_guide 获取该物种的护理规范；严禁把不适用的病症、处置或记录类型安到对应物种上（例如鱼类不存在"腹泻"这一常见病症框架、鸟类不接种常规疫苗）；当用户的问题与物种不符时，应温和指出并给出该物种的正确方向。
8. 帮用户记录健康事项（如"帮我记一笔……"）时，必须调用 create_record_draft 工具起草草稿并请用户确认，严禁不调用工具就直接输出"草稿"样式的文字，严禁声称已直接保存；草稿中相对日期（昨天/下周四/下个月等）先按今天换算为 YYYY-MM-DD。

""" + TOOLS_BRIEF

# ---------------------------------------------------------------- LangChain Agent

_agent = None
_agent_failed = False


def _build_llm():
    prov = provider()
    if prov == "deepseek":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            api_key=deepseek_key(),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            temperature=0.3,
            timeout=60,
        ), "deepseek"
    from langchain_community.chat_models.tongyi import ChatTongyi
    return ChatTongyi(
        model=os.environ.get("QWEN_MODEL", "qwen-plus"),
        dashscope_api_key=dashscope_key(),
        temperature=0.3,
    ), "tongyi"


def _build_tools():
    """StructuredTool + 显式 schema：适配 DeepSeek 原生 function calling 的结构化参数。"""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class NameIn(BaseModel):
        name: str = Field(description="宠物的名字，如：可乐")

    class Empty(BaseModel):
        pass

    class ReportIn(BaseModel):
        name: str = Field(default="", description="宠物名字；留空表示生成全部宠物的报告")
        period: str = Field(default="月", description="统计周期：周 / 月 / 年")

    class MemNameIn(BaseModel):
        name: str = Field(default="", description="宠物名字；留空表示查看全部宠物的回忆")

    class DraftIn(BaseModel):
        pet_name: str = Field(description="宠物名字")
        record_type: str = Field(description="记录类型：vaccine|checkup|deworm|medication|clinic 或中文（疫苗/体检/驱虫/喂药/就诊）")
        date: str = Field(description="记录日期 YYYY-MM-DD；相对日期先按今天换算")
        title: str = Field(description="记录标题")
        note: str = Field(default="", description="补充说明")
        next_date: str = Field(default="", description="下次日期 YYYY-MM-DD，没有则留空")
        weight: float | None = Field(default=None, description="当时体重 kg，没有则省略")

    return [
        StructuredTool.from_function(tools.query_pet, name="query_pet",
                                     description=tools.query_pet.__doc__.strip(),
                                     args_schema=NameIn),
        StructuredTool.from_function(tools.query_health_records,
                                     name="query_health_records",
                                     description=tools.query_health_records.__doc__.strip(),
                                     args_schema=NameIn),
        StructuredTool.from_function(lambda: tools.get_reminders(""),
                                     name="get_reminders",
                                     description=tools.get_reminders.__doc__.strip(),
                                     args_schema=Empty),
        StructuredTool.from_function(tools.analyze_health, name="analyze_health",
                                     description=tools.analyze_health.__doc__.strip(),
                                     args_schema=NameIn),
        StructuredTool.from_function(
            lambda name="", period="月": tools.generate_report(name, period),
            name="generate_report",
            description=tools.generate_report.__doc__.strip(),
            args_schema=ReportIn),
        StructuredTool.from_function(
            lambda name="": tools.query_memories(name),
            name="query_memories",
            description=tools.query_memories.__doc__.strip(),
            args_schema=MemNameIn),
        StructuredTool.from_function(
            lambda name="": tools.get_care_guide(name),
            name="get_care_guide",
            description=tools.get_care_guide.__doc__.strip(),
            args_schema=MemNameIn),
        StructuredTool.from_function(
            tools.create_record_draft,
            name="create_record_draft",
            description=tools.create_record_draft.__doc__.strip(),
            args_schema=DraftIn),
    ]


def _build_langchain_agent():
    """构建 LangChain 1.x create_agent 工具调用图；失败抛异常（由调用方降级）。"""
    global _agent
    if _agent is not None:
        return _agent
    from langchain.agents import create_agent

    llm, _prov = _build_llm()
    _agent = create_agent(model=llm, tools=_build_tools(), system_prompt=SYSTEM_PROMPT)
    return _agent


def _ask_agent(message: str, history: list[dict] | None = None) -> str:
    global _agent_failed
    try:
        agent = _build_langchain_agent()
    except Exception as e:  # 构建失败（依赖缺失/配置错误）→ 本进程内熔断
        _agent_failed = True
        return (f"⚠️ Agent 构建失败（{type(e).__name__}: {e}），已自动切换到示例回答模式。\n\n"
                + _example_answer(message))
    try:
        msgs = [{"role": h["role"], "content": h["content"]} for h in (history or [])]
        from datetime import date as _date
        msgs.append({"role": "user",
                     "content": f"[系统注：今天是 {_date.today().isoformat()}]\n{message}"})
        result = agent.invoke({"messages": msgs}, config={"recursion_limit": 12})
        for m in reversed(result.get("messages", [])):
            if getattr(m, "type", "") in ("ai", "assistant") and str(getattr(m, "content", "")).strip():
                return str(m.content).strip()
        return "（模型未返回内容，请重试）"
    except Exception as e:  # 单次调用失败（网络/额度等）→ 本次降级，下次仍会重试 Agent
        return (f"⚠️ Agent 本次调用失败（{type(e).__name__}: {e}），本条为示例回答。\n\n"
                + _example_answer(message))


# ---------------------------------------------------------------- 无 Key 降级

def _find_pet_name(message: str) -> str | None:
    """从问题中提取可能出现的宠物名（与库中名字做子串匹配）。"""
    import db
    for p in db.list_pets():
        if p["name"] and p["name"] in message:
            return p["name"]
    return None


def db_reminders_of(pet_name: str) -> list[dict]:
    import db
    pet = db.fetch_pet_by_name(pet_name)
    if not pet:
        return []
    return [r for r in db.compute_reminders() if r["pet_id"] == pet["id"]]


def _example_answer(message: str) -> str:
    """不依赖大模型：解析关键词 → 调用同一套工具 → 用数据库真实数据拼中文回答。"""
    msg = message.strip()
    pet = _find_pet_name(msg)
    report_kw = any(k in msg for k in ("报告", "周报", "月报", "报表"))
    memory_kw = any(k in msg for k in ("回忆", "故事", "第一次", "纪念", "成长", "照片"))
    remind_kw = any(k in msg for k in ("提醒", "到期", "临期", "逾期", "该做", "什么时候"))
    analyze_kw = any(k in msg for k in ("分析", "怎么样", "健康吗", "体重", "状态如何", "评估"))
    record_kw = any(k in msg for k in ("疫苗", "驱虫", "体检", "喂药", "就诊", "记录", "打过", "接种"))
    care_kw = any(k in msg for k in ("该做", "不该做", "禁忌", "能吃", "不能吃", "注意什么", "护理", "照顾", "规范"))

    if care_kw:
        return (tools.get_care_guide(pet or "")
                + "\n\n> 当前为示例回答模式（未配置 API Key）。")

    if memory_kw and not report_kw:
        return (tools.query_memories(pet or "")
                + "\n\n> 当前为示例回答模式（未配置 API Key）。")
    if report_kw:
        return (tools.generate_report(pet or "", "月")
                + "\n\n> 当前为示例回答模式（未配置 API Key），报告由数据库真实数据自动生成。")
    if pet and analyze_kw and not record_kw:
        return tools.analyze_health(pet) + "\n\n> 当前为示例回答模式（未配置 API Key）。"
    if remind_kw and not pet:
        return tools.get_reminders() + "\n\n> 当前为示例回答模式（未配置 API Key）。"
    if pet and (record_kw or remind_kw):
        head = tools.query_pet(pet) + "\n\n" + tools.query_health_records(pet)
        if remind_kw:
            rows = db_reminders_of(pet)
            if rows:
                head += "\n\n" + "\n".join(
                    f"- {r['title']}（{r['type_label']}）" +
                    (f"已逾期 {-r['days_left']} 天" if r["overdue"] else f"还剩 {r['days_left']} 天")
                    for r in rows)
        return head + "\n\n> 当前为示例回答模式（未配置 API Key）。"
    if pet:
        return (tools.query_pet(pet) + "\n\n" + tools.analyze_health(pet)
                + "\n\n> 当前为示例回答模式（未配置 API Key）。")
    if remind_kw:
        return tools.get_reminders() + "\n\n> 当前为示例回答模式（未配置 API Key）。"

    # 兜底：介绍能力 + 系统概览
    import db
    st = db.stats()
    return ("你好！我是智能宠物健康管家（当前为示例回答模式，未配置 API Key）。\n"
            f"系统中共有 {st['pet_count']} 只宠物、{st['record_count']} 条健康记录、"
            f"{st['due_count']} 项临期/逾期提醒。\n"
            "你可以这样问我：\n"
            "- 「可乐接下来要打什么疫苗？」\n"
            "- 「布丁的健康状况怎么样？」\n"
            "- 「最近有哪些到期的事项？」\n"
            "- 「生成一只宠物的健康报告」\n"
            "配置 DEEPSEEK_API_KEY（或 DASHSCOPE_API_KEY）后，我将作为 LangChain Agent 理解任意提问并自动调用工具。")


# ---------------------------------------------------------------- 今日健康简报

BRIEFING_PROMPT = (
    "请阅读数据库现状，生成一段 150 字以内的「今日健康简报」，要求："
    "1) 一句话概述宠物与记录规模；"
    "2) 逐条列出临期/逾期事项并各给一句具体建议；"
    "3) 如有体重明显波动的宠物提一句；"
    "4) 收尾一句温暖克制的总结。直接输出简报正文，不要大标题。"
)


def briefing_fallback() -> str:
    """无 Key 时的规则拼接简报（数据同样来自真实库）。"""
    import db
    st = db.stats()
    rem = db.compute_reminders()
    lines = [f"目前共有 {st['pet_count']} 只宠物、{st['record_count']} 条健康记录。"]
    if rem:
        items = "；".join(
            f"{r['pet_name']}的{r['type_label']}「{r['title']}」"
            + (f"已逾期 {-r['days_left']} 天" if r["overdue"] else f"还剩 {r['days_left']} 天")
            for r in rem[:4])
        lines.append("需要关注：" + items + "，建议尽快安排处理。")
    else:
        lines.append("当前没有临期或逾期事项，一切都在计划内。")
    lines.append("打开 AI 助手可以继续询问任意宠物的详细情况。")
    return "\n".join(lines)


def generate_briefing() -> dict:
    """生成今日简报：有 Key 走 Agent（真实生成），无 Key 规则拼接。返回 {text, mode}。"""
    if provider() and not _agent_failed:
        try:
            result = _ask_agent(BRIEFING_PROMPT)
            if not result.startswith("⚠️"):
                return {"text": result.strip(), "mode": "agent"}
        except Exception:
            pass
    return {"text": briefing_fallback(), "mode": "example"}


# ---------------------------------------------------------------- 体重 AI 解读

def _weight_fallback(pet: dict, weights: list) -> str:
    """无 Key 时的规则版体重解读。"""
    w0, w1 = weights[0], weights[-1]
    delta = w1["weight"] - w0["weight"]
    pct = abs(delta / w0["weight"] * 100) if w0["weight"] else 0
    trend = "上升" if delta > 0.005 else ("下降" if delta < -0.005 else "平稳")
    advice = ("体重波动较大，建议咨询兽医调整饮食与运动计划。"
              if pct >= 10 else "波动幅度在正常范围内，建议继续保持定期称重与观察。")
    return (f"体重从 {w0['weight']}kg 变化到 {w1['weight']}kg"
            f"（{w0['date']} 至 {w1['date']}，{trend}约 {round(pct, 1)}%）。{advice}")


def weight_insight(pet_id: int) -> dict:
    """体重趋势 AI 解读：结合体重序列与近期记录生成，按数据签名缓存。"""
    import db
    import species
    pet = db.get_pet(pet_id)
    if not pet:
        return {"text": "宠物不存在。", "mode": "example", "cached": True}
    weights = db.list_weight_logs(pet_id)
    records = db.list_records(pet_id)[:5]
    if len(weights) < 2:
        return {"text": "体重记录还不足两条，暂无趋势可解读。通过「＋ 记体重」积累几次数据后再来。",
                "mode": "example", "cached": True}
    sig = f"{len(weights)}|{weights[-1]['weight']}|{weights[-1]['date']}|{records[0]['id'] if records else 0}"
    key = f"weight-insight:{pet_id}"
    meta = db.kv_get_meta(key)
    if meta:
        try:
            data = json.loads(meta["value"])
            if data.get("sig") == sig:
                return {"text": data["text"], "mode": data.get("mode", "agent"),
                        "cached": True, "generated_at": meta["updated_at"]}
        except Exception:
            pass
    if provider() and not _agent_failed:
        w_lines = "\n".join(f"- {w['date']}：{w['weight']} kg" for w in weights)
        r_lines = "\n".join(f"- {r['date']}【{r['type_label']}】{r['title']}" for r in records) or "- 暂无"
        prompt = (f"请对宠物「{pet['name']}」（{species.type_label(pet['type'])}）的体重趋势给出 80 字以内的解读，"
                  "内容包括：变化方向与幅度是否合理、可能原因、一条可执行的建议。直接输出解读正文。\n"
                  f"体重记录：\n{w_lines}\n近期健康记录：\n{r_lines}")
        try:
            result = _ask_agent(prompt)
            if result.startswith("⚠️"):
                text, mode = _weight_fallback(pet, weights), "example"
            else:
                text, mode = result.strip(), "agent"
        except Exception:
            text, mode = _weight_fallback(pet, weights), "example"
    else:
        text, mode = _weight_fallback(pet, weights), "example"
    db.kv_set(key, json.dumps({"text": text, "mode": mode, "sig": sig}, ensure_ascii=False))
    return {"text": text, "mode": mode, "cached": False}


# ---------------------------------------------------------------- 对外入口

def answer(message: str, history: list[dict] | None = None) -> dict:
    """返回 {"reply": 回答文本, "mode": "agent"|"example"}。

    history：最近多轮对话 [{role: user|assistant, content}, ...]（时间正序），
    让 Agent 支持追问（如"那它的体重呢？"）；示例回答模式为单轮，忽略历史。
    """
    if provider() and not _agent_failed:
        reply = _ask_agent(message, history)
        if reply.startswith("⚠️"):
            return {"reply": reply, "mode": "example"}
        return {"reply": reply, "mode": "agent"}
    return {"reply": _example_answer(message), "mode": "example"}


def status() -> dict:
    """供前端探测当前运行模式。"""
    prov = provider()
    live = bool(prov and not _agent_failed)
    return {"mode": "agent" if live else "example",
            "has_key": bool(prov),
            "provider": prov}
