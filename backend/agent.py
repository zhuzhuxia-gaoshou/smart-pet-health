# -*- coding: utf-8 -*-
"""agent.py — LangChain ReAct Agent（通义千问）+ 无 Key 降级"示例回答"模式。

对外入口：answer(message) -> {"reply": str, "mode": "agent"|"example"}
"""
import os
import re

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


def api_key() -> str:
    _load_env()
    return os.environ.get("DASHSCOPE_API_KEY", "").strip()


SYSTEM_PROMPT = """你是「智能宠物健康管家」的 AI 助手，一个专业的宠物健康管理 Agent。
你通过工具查询 SQLite 数据库中的真实宠物档案与健康记录，请遵循：
1. 回答宠物、疫苗、体检、驱虫、喂药、就诊、提醒、体重、报告相关问题前，必须先调用相应工具获取真实数据，严禁编造数据。
2. 用简体中文回答，语气专业、温暖、克制。
3. 涉及医疗判断时，提醒用户以兽医意见为准。
4. 数据不足时诚实说明，并建议补充记录。
5. 生成报告时使用 Markdown 格式。

可用工具：
- query_pet: 按名字查宠物基本信息
- query_health_records: 按名字查健康记录
- get_reminders: 查所有临期/逾期事项
- analyze_health: 按名字做健康分析
- generate_report: 生成健康报告（参数格式 "宠物名,周期"，名字留空表示全部）

按 ReAct 格式工作：
Question: 用户问题
Thought: 我需要调用什么工具
Action: 工具名，如 query_pet
Action Input: 工具参数
Observation: 工具返回结果
...（可多轮）
Thought: 我已经掌握足够信息
Final Answer: 最终中文回答"""


# ---------------------------------------------------------------- LangChain Agent

_agent = None
_agent_failed = False


def _build_langchain_agent():
    """构建 LangChain ReAct Agent；依赖缺失或构建失败时抛异常。"""
    global _agent
    if _agent is not None:
        return _agent
    from langchain_community.llms import Tongyi
    from langchain_core.prompts import PromptTemplate
    from langchain_core.tools import Tool
    from langchain.agents import AgentExecutor, create_react_agent

    llm = Tongyi(
        model=os.environ.get("QWEN_MODEL", "qwen-plus"),
        dashscope_api_key=api_key(),
        temperature=0.3,
    )
    langchain_tools = [
        Tool(name="query_pet", description=tools.query_pet.__doc__.strip(), func=tools.query_pet),
        Tool(name="query_health_records",
             description=tools.query_health_records.__doc__.strip(),
             func=tools.query_health_records),
        Tool(name="get_reminders", description=tools.get_reminders.__doc__.strip(),
             func=lambda _in="": tools.get_reminders()),
        Tool(name="analyze_health", description=tools.analyze_health.__doc__.strip(),
             func=tools.analyze_health),
        Tool(name="generate_report", description=tools.generate_report.__doc__.strip(),
             func=lambda _in="": _parse_report_args(_in)),
    ]
    template = SYSTEM_PROMPT + """

{chat_history}
Question: {input}
{agent_scratchpad}"""
    prompt = PromptTemplate.from_template(
        template, input_variables=["input", "agent_scratchpad", "chat_history"])
    agent = create_react_agent(llm=llm, tools=langchain_tools, prompt=prompt)
    _agent = AgentExecutor(agent=agent, tools=langchain_tools, handle_parsing_errors=True,
                           max_iterations=6, verbose=False, return_intermediate_steps=False)
    return _agent


def _parse_report_args(arg: str) -> str:
    """generate_report 的 Action Input 兼容 '名字,周期' / '名字' / 空。"""
    arg = (arg or "").strip()
    if not arg:
        return tools.generate_report("", "月")
    parts = [p.strip() for p in re.split(r"[,，\s]+", arg) if p.strip()]
    if len(parts) == 1:
        return tools.generate_report(parts[0], "月")
    return tools.generate_report(parts[0], parts[1])


def _ask_agent(message: str) -> str:
    global _agent_failed
    try:
        executor = _build_langchain_agent()
        result = executor.invoke({"input": message, "chat_history": ""})
        return str(result.get("output", "")).strip() or "（模型未返回内容，请重试）"
    except Exception as e:  # LangChain 未装好 / API 报错 → 降级
        _agent_failed = True
        return f"⚠️ Agent 调用失败（{type(e).__name__}: {e}），已自动切换到示例回答模式。\n\n" \
               + _example_answer(message)


# ---------------------------------------------------------------- 无 Key 降级

def _find_pet_name(message: str) -> str | None:
    """从问题中提取可能出现的宠物名（与库中名字做子串匹配）。"""
    import db
    for p in db.list_pets():
        if p["name"] and p["name"] in message:
            return p["name"]
    return None


def _example_answer(message: str) -> str:
    """不依赖大模型：解析关键词 → 调用同一套工具 → 用数据库真实数据拼中文回答。"""
    msg = message.strip()
    pet = _find_pet_name(msg)
    report_kw = any(k in msg for k in ("报告", "周报", "月报", "报表"))
    remind_kw = any(k in msg for k in ("提醒", "到期", "临期", "逾期", "该做", "什么时候"))
    analyze_kw = any(k in msg for k in ("分析", "怎么样", "健康吗", "体重", "状态如何", "评估"))
    record_kw = any(k in msg for k in ("疫苗", "驱虫", "体检", "喂药", "就诊", "记录", "打过", "接种"))

    if report_kw:
        return (tools.generate_report(pet or "", "月")
                + "\n\n> 当前为示例回答模式（未配置 API Key），报告由数据库真实数据自动生成。")
    if pet and analyze_kw and not record_kw:
        return (tools.analyze_health(pet)
                + "\n\n> 当前为示例回答模式（未配置 API Key）。")
    if remind_kw and not pet:
        return (tools.get_reminders()
                + "\n\n> 当前为示例回答模式（未配置 API Key）。")
    if pet and (record_kw or remind_kw):
        head = tools.query_pet(pet) + "\n\n" + tools.query_health_records(pet)
        if remind_kw:
            head += "\n\n" + "\n".join(
                f"- {r['title']}（{r['type_label']}）" +
                (f"已逾期 {-r['days_left']} 天" if r["overdue"] else f"还剩 {r['days_left']} 天")
                for r in db_reminders_of(pet)) or ""
        return head + "\n\n> 当前为示例回答模式（未配置 API Key）。"
    if pet:
        return (tools.query_pet(pet) + "\n\n" + tools.analyze_health(pet)
                + "\n\n> 当前为示例回答模式（未配置 API Key）。")
    if remind_kw:
        return (tools.get_reminders()
                + "\n\n> 当前为示例回答模式（未配置 API Key）。")

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
            "配置 DASHSCOPE_API_KEY 后，我将作为 LangChain ReAct Agent 理解任意提问并自动调用工具。")


def db_reminders_of(pet_name: str) -> list[dict]:
    import db
    pet = db.fetch_pet_by_name(pet_name)
    if not pet:
        return []
    return [r for r in db.compute_reminders() if r["pet_id"] == pet["id"]]


# ---------------------------------------------------------------- 对外入口

def answer(message: str) -> dict:
    """返回 {"reply": 回答文本, "mode": "agent"|"example"}。"""
    key = api_key()
    if key and not key.startswith("sk-xxx") and not _agent_failed:
        reply = _ask_agent(message)
        if not reply.startswith("⚠️"):
            return {"reply": reply, "mode": "agent"}
        return {"reply": reply, "mode": "example"}
    return {"reply": _example_answer(message), "mode": "example"}


def status() -> dict:
    """供前端探测当前运行模式。"""
    key = api_key()
    live = bool(key and not key.startswith("sk-xxx"))
    return {"mode": "agent" if (live and not _agent_failed) else "example",
            "has_key": live}
