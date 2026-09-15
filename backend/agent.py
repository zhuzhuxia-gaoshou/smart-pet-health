# -*- coding: utf-8 -*-
"""agent.py — LangChain Agent（DeepSeek 工具调用 / 通义千问 ReAct）+ 无 Key 降级"示例回答"模式。

- 配置 DEEPSEEK_API_KEY   → DeepSeek Chat（OpenAI 兼容端点，原生 function calling 驱动 ReAct 循环）
- 仅配置 DASHSCOPE_API_KEY → 通义千问 ChatTongyi（若不支持工具调用则自动降级）
- 都未配置                → ExampleAgent（解析关键词，调用同一套工具查数据库拼回答）

对外入口：answer(message) -> {"reply": str, "mode": "agent"|"example"}
"""
import json
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


def _key(name: str) -> str:
    _load_env()
    v = os.environ.get(name, "").strip()
    return "" if v.startswith("sk-xxx") else v


def deepseek_key() -> str:
    return _key("DEEPSEEK_API_KEY")


def dashscope_key() -> str:
    return _key("DASHSCOPE_API_KEY")


def bailian_key() -> str:
    return _key("BAILIAN_API_KEY")


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
- create_record_draft(宠物名, 类型, 日期, 标题, 说明, 下次日期, 体重): 用户口述要记一笔健康事项时调用，起草待确认的记录草稿（不直接入库）
- get_attention_ranking(): 多宠物关注优先级排序，回答"我该先管哪只"类问题；无需参数
- query_medications(宠物名): 查该宠物的用药情况——在用药物的剂量/频次/疗程剩余天数与已结束的用药历史"""

SYSTEM_PROMPT = """你是「智能宠物健康管家」的 AI 助手，一个专业的宠物健康管理 Agent。
你通过工具查询 SQLite 数据库中的真实宠物档案与健康记录，请遵循：
1. 回答宠物、疫苗、体检、驱虫、喂药、用药方案、就诊、提醒、体重、报告相关问题前，必须先调用工具获取真实数据，严禁编造数据。
2. 需要多个信息时可以依次调用多个工具。
3. 用简体中文回答，语气专业、温暖、克制，适当使用 Markdown 排版。
4. 涉及医疗判断时，提醒用户以兽医意见为准。
5. 数据不足时诚实说明，并建议补充记录。
6. 生成报告时输出完整 Markdown 文本。
7. 物种边界：不同宠物生理差异极大。回答护理、疾病、饮食类问题前，先通过 query_pet 确认宠物类型，必要时调用 get_care_guide 获取该物种的护理规范；严禁把不适用的病症、处置或记录类型安到对应物种上（例如鱼类不存在"腹泻"这一常见病症框架、鸟类不接种常规疫苗）；当用户的问题与物种不符时，应温和指出并给出该物种的正确方向。
8. 帮用户记录健康事项（如"帮我记一笔……"）时，必须调用 create_record_draft 工具起草草稿并请用户确认，严禁不调用工具就直接输出"草稿"样式的文字，严禁声称已直接保存；草稿中相对日期（昨天/下周四/下个月等）先按今天换算为 YYYY-MM-DD。

""" + TOOLS_BRIEF

# ---------------------------------------------------------------- 分层多专家：路由 → 专家 → 降级
# 拓扑：规则路由（0ms、可归因）→ 三位专家（各自提示词 + 工具子集）→ 失败降级到通用 Agent → 规则模式。
# 门禁不变式：全系统唯一写操作仍是建档草稿，且唯一入口是 care_advisor 的 create_record_draft → 前端确认卡。
# 禁止给任何专家注册直接写库的工具。

_MED_NOTE = "涉及医疗判断（是否生病、是否停药、指标是否异常）时，明确提示「请以兽医意见为准」。用简体中文，专业、温暖、克制。"

ANALYST_PROMPT = """你是「智能宠物健康管家」的【健康分析师】。你的职责：基于数据库真实数据，回答事实查询与状况评估类问题——宠物档案、健康记录（疫苗/体检/驱虫/喂药/就诊）、临期与逾期提醒、综合健康分析、用药情况、多宠物的关注优先级。

你只负责"查与析"，不负责：护理操作建议、生成正式报告、为用户起草健康记录。遇到这三类需求时，用一句话说明"这个问题更适合护理顾问或报告功能处理"即可，不要越界作答。

规则：
1. 回答前必须先调用工具获取真实数据，严禁编造任何数字、日期、药名；需要多个信息时可依次调用多个工具（如先 query_pet 确认宠物存在，再 analyze_health）。
2. 回答结构：第一句直接给结论；随后用列表列出关键事实；保留工具输出的原始结论词（如"已逾期 X 天""还剩 X 天""体重波动超过 10%"），不要改写或省略。
3. 物种边界：作答前先经 query_pet 确认物种；鸟类不接种常规疫苗、鱼类不存在"腹泻"这一常见病症框架。当问题与该宠物物种不符时，温和指出并给出该物种的正确方向。
4. 数据不足时诚实说明，并建议用户补充记录。
5. """ + _MED_NOTE

ADVISOR_PROMPT = """你是「智能宠物健康管家」的【护理顾问】。你的职责：物种护理规范问答（能吃什么、禁忌、该做与不该做的事、日常照护）、基于物种的日常建议，以及帮用户起草健康记录。

你只负责"护与记"，不负责：生成正式健康报告、多宠物数据分析。此类需求请一句话引导用户换个问法，不要越界作答。

规则：
1. 回答护理问题前，先 query_pet 确认宠物与物种，再调用 get_care_guide 获取该物种的护理规范；规范之外的常识性建议须标注"一般性建议"。
2. 物种边界是硬约束：鸟类不接种常规疫苗、鱼类不存在"腹泻"这一常见病症框架、药物剂量因物种与体重差异极大；严禁把 A 物种的病症、处置或记录类型套到 B 物种上。当用户的问题与物种不符时，温和纠正并给出该物种的正确方向。
3. 用药与剂量问题只转述数据库已有信息（query_medications），可给一般性提醒，但必须说明"具体剂量与用药方案请以兽医意见为准"，严禁自行推荐具体药品或剂量。
4. 用户口述要记一笔健康事项（"记一笔/记一下/打完疫苗了/复诊回来"等）时，必须调用 create_record_draft 工具起草草稿，并请用户在确认卡片中确认或取消；严禁不调用工具就输出草稿样式的文字，严禁声称已直接保存。草稿中相对日期（昨天/下周四等）先按今天换算为 YYYY-MM-DD。
5. 若工具拒绝起草（该记录类型不适用于该物种），向用户解释原因，不要反复重试。
6. 建议结构：分"现在可以做 / 需要避免 / 什么情况要就医"三段。""" + _MED_NOTE

WRITER_PROMPT = """你是「智能宠物健康管家」的【报告撰稿人】。你的职责：生成结构化健康报告与成长回顾类内容。

规则：
1. 生成健康报告必须调用 generate_report 工具获取结构化数据，在其基础上撰写：开头补一段两三句的导语（概述整体状况与最需关注点），正文保留工具输出的全部事实、数字与结论；可调整措辞，但严禁增删改任何数字、日期与结论词。
2. 输出必须是完整 Markdown，且第一行为「# 」开头的一级标题（界面依赖标题识别报告并提供下载）。
3. 成长回顾类问题调用 query_memories，基于真实回忆撰写，可润色情感表达，但不得虚构事件。
4. 报告中的警示结论（逾期、体重波动超过 10%、治疗中）原样保留 ⚠️ 标记，并在结尾注明"本报告由系统数据自动生成，健康判断请以兽医意见为准"。
5. 多宠物报告保持 generate_report 的分节结构。用简体中文，文风克制、专业，不堆砌形容词。"""

# 专家注册表：tools=None 表示全部工具（通用兜底）
EXPERTS = {
    "health_analyst": {"label": "健康分析师", "prompt": ANALYST_PROMPT,
                       "tools": ["query_pet", "query_health_records", "get_reminders", "analyze_health",
                                 "query_medications", "get_attention_ranking"]},
    "care_advisor":   {"label": "护理顾问", "prompt": ADVISOR_PROMPT,
                       "tools": ["query_pet", "get_care_guide", "query_medications", "get_reminders",
                                 "create_record_draft"]},
    "report_writer":  {"label": "报告撰稿人", "prompt": WRITER_PROMPT,
                       "tools": ["generate_report", "query_pet", "query_health_records", "query_memories"]},
    "general_agent":  {"label": "通用助手", "prompt": SYSTEM_PROMPT, "tools": None},
}

# 路由规则（按优先级；建档意图永远最高，保证 main.py 的草稿兜底重试必中 care_advisor）
_ROUTE_DRAFT = re.compile(r"记一笔|记一下|记录一下|帮我记|帮我登记|登记一下|补充一条|添加一条记录|create_record_draft|起草")
_ROUTE_REPORT = re.compile(r"报告|周报|月报|年报|报表|总结|成长回顾|回忆|故事|第一次")
_ROUTE_CARE = re.compile(r"能吃|不能吃|可以吃|禁忌|该做|不该做|怎么照顾|照顾|护理|注意什么|怎么办|换羽|能不能|可不可以|注意事项")
_ROUTE_HEALTH = re.compile(r"疫苗|驱虫|体检|用药|吃药|什么药|药物|剂量|体重|健康|分析|记录|提醒|到期|临期|逾期|过期|优先|先管|就诊|复诊|三联|狂犬|打针|接种")
# 弱信号（"怎么样/多大"等）单独出现太泛（"今天天气怎么样"），只在句中带库内宠物名时才算健康问题
_ROUTE_HEALTH_WEAK = re.compile(r"怎么样|状况|多大|多重|几岁|情况|正常吗")
_VACCINE_WORDS = re.compile(r"疫苗|驱虫|接种")


def _route(message: str) -> tuple[str, str]:
    """返回 (专家名, 路由来源)。规则优先、确定性；未命中落通用兜底。"""
    msg = (message or "").strip()
    if _ROUTE_DRAFT.search(msg):
        return "care_advisor", "rule:draft"
    # 物种边界动态规则：非猫狗宠物 + 疫苗/驱虫词 → 交给护理顾问纠正（如"翠翠该打什么疫苗"）
    pet_name = _find_pet_name(msg)
    if pet_name and _VACCINE_WORDS.search(msg):
        import db
        pet = db.fetch_pet_by_name(pet_name)
        if pet and pet.get("type") not in ("cat", "dog"):
            return "care_advisor", "rule:species-boundary"
    if _ROUTE_REPORT.search(msg):
        return "report_writer", "rule:report"
    if _ROUTE_CARE.search(msg):
        return "care_advisor", "rule:care"
    if _ROUTE_HEALTH.search(msg) or (pet_name and _ROUTE_HEALTH_WEAK.search(msg)):
        return "health_analyst", "rule:health"
    return "general_agent", "default"

# ---------------------------------------------------------------- LangChain Agent

_agent_cache: dict = {}          # 按 provider 缓存已构建的 Agent
_dead_providers: set = set()     # 欠费等持续性错误 → 本进程内熔断该供应商
_agent_failed = False


def _build_llm(prov: str):
    if prov == "bailian":
        # 百炼通义千问（Anthropic 兼容端点，如 qwen3.8-flash）
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=os.environ.get("BAILIAN_MODEL", "qwen3.8-flash"),
            api_key=bailian_key(),
            base_url=os.environ.get("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/apps/anthropic"),
            temperature=0.3,
            timeout=60,
            max_retries=1,
        )
    if prov == "deepseek":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            api_key=deepseek_key(),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            temperature=0.3,
            timeout=60,
        )
    from langchain_community.chat_models.tongyi import ChatTongyi
    return ChatTongyi(
        model=os.environ.get("QWEN_MODEL", "qwen-plus"),
        dashscope_api_key=dashscope_key(),
        temperature=0.3,
    )


def _llm_candidates() -> list:
    """按优先级返回可用供应商：百炼优先 → DeepSeek → 通义，失败自动顺延。"""
    cands = []
    if bailian_key():
        cands.append("bailian")
    if deepseek_key():
        cands.append("deepseek")
    if dashscope_key():
        cands.append("tongyi")
    return cands


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
        StructuredTool.from_function(lambda: tools.get_attention_ranking(),
                                     name="get_attention_ranking",
                                     description=tools.get_attention_ranking.__doc__.strip(),
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
        StructuredTool.from_function(tools.query_medications, name="query_medications",
                                     description=tools.query_medications.__doc__.strip(),
                                     args_schema=NameIn),
    ]


_TOOL_REGISTRY: dict | None = None


def _tool_registry() -> dict:
    """StructuredTool 无状态，构建一次按名字复用，供各专家按子集取用。"""
    global _TOOL_REGISTRY
    if _TOOL_REGISTRY is None:
        _TOOL_REGISTRY = {t.name: t for t in _build_tools()}
    return _TOOL_REGISTRY


def _expert_prompt(profile: str) -> str:
    """专家系统提示词 + 该专家可用工具的简介（通用助手已自带 TOOLS_BRIEF）。"""
    spec = EXPERTS[profile]
    if spec["tools"] is None:
        return spec["prompt"]
    desc = {name: d for name, d in tools.TOOL_META}
    brief = "\n".join(f"- {n}: {desc.get(n, '')}" for n in spec["tools"])
    return spec["prompt"] + "\n\n可用工具：\n" + brief


def _get_agent(prov: str, profile: str = "general_agent"):
    """按 (供应商, 专家) 构建并缓存 Agent；失败抛异常（由调用方处理）。"""
    key = (prov, profile)
    if key in _agent_cache:
        return _agent_cache[key]
    from langchain.agents import create_agent
    spec = EXPERTS[profile]
    reg = _tool_registry()
    tool_list = list(reg.values()) if spec["tools"] is None else [reg[n] for n in spec["tools"]]
    obj = create_agent(model=_build_llm(prov), tools=tool_list, system_prompt=_expert_prompt(profile))
    _agent_cache[key] = obj
    return obj


def _extract_ai_text(result) -> str:
    """从 Agent 结果里取最后一条有效 AI 文本；Anthropic 风格块数组只取 text 块（忽略 thinking）。"""
    for m in reversed(result.get("messages", [])):
        if getattr(m, "type", "") not in ("ai", "assistant"):
            continue
        content = getattr(m, "content", "")
        if isinstance(content, list):
            text = "\n".join(b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text").strip()
        else:
            text = str(content).strip()
        if text:
            return text
    return ""


def _ask_with(profile: str, message: str, history: list[dict] | None = None) -> str:
    """用指定专家按供应商链回答（百炼 → DeepSeek → 通义）。
    欠费等持续性错误熔断该供应商（全专家共享）；空回复/其余错误仅跳过本次。
    全部失败返回以 ⚠️ 开头的说明文本（调用方据此决定降级）。"""
    global _agent_failed
    msgs = [{"role": h["role"], "content": h["content"]} for h in (history or [])]
    from datetime import date as _date
    msgs.append({"role": "user",
                 "content": f"[系统注：今天是 {_date.today().isoformat()}]\n{message}"})
    last_err = None
    for prov in _llm_candidates():
        if prov in _dead_providers:
            continue
        try:
            agent = _get_agent(prov, profile)
            result = agent.invoke({"messages": msgs}, config={"recursion_limit": 12})
            text = _extract_ai_text(result)
            if text:
                return text
            last_err = RuntimeError("模型未返回内容")
        except Exception as e:
            last_err = e
            text = str(e)
            if "402" in text or "Insufficient Balance" in text or "Arrearage" in text:
                _dead_providers.add(prov)  # 欠费是持续状态 → 本进程内熔断该供应商
    _agent_failed = not _llm_candidates()
    if last_err is not None:
        return f"⚠️ {EXPERTS[profile]['label']}本次调用失败（{type(last_err).__name__}: {last_err}）"
    return "⚠️ 没有可用的模型供应商"


def _ask_agent(message: str, history: list[dict] | None = None) -> str:
    """通用 Agent（全工具）直答；供简报/体重解读/护理计划复用。失败时附带示例回答。"""
    reply = _ask_with("general_agent", message, history)
    if reply.startswith("⚠️"):
        return reply + "，本条为示例回答。\n\n" + _example_answer(message)
    return reply


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
    med_kw = any(k in msg for k in ("用药", "吃药", "什么药", "药物", "剂量", "停药", "疗程", "在吃"))

    if care_kw:
        return (tools.get_care_guide(pet or "")
                + "\n\n> 当前为示例回答模式（未配置 API Key）。")

    if pet and med_kw:
        return tools.query_medications(pet) + "\n\n> 当前为示例回答模式（未配置 API Key）。"

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
    "4) 可调用 get_attention_ranking 确定优先级，最后一行单独写「❗ 最需要关注：XX（一句理由）」；"
    "5) 收尾一句温暖克制的总结。直接输出简报正文，不要大标题。"
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


# ---------------------------------------------------------------- AI 护理计划

# 各记录类型的默认复做周期（天）
PLAN_CYCLE = {"vaccine": 365, "checkup": 365, "deworm": 30, "medication": 7, "clinic": 14}


def _plan_fallback_summary() -> str:
    return "已根据物种习性与历史记录生成本月护理计划，请逐项核对后转为正式记录。"


def generate_care_plan(pet_id: int) -> dict:
    """月度护理计划：规则引擎生成结构化计划项（可一键转记录），AI 补一段总结评语。"""
    import db
    import species
    from datetime import date, timedelta
    pet = db.get_pet(pet_id)
    if not pet:
        return {"error": "宠物不存在"}
    today = date.today()
    records = db.list_records(pet_id)
    allowed = species.allowed_record_types(pet["type"])
    items, seen = [], set()

    # ① 未来 30 天内到期的记录：安排在到期日复做，并按周期预排下一次
    for r in records:
        if not r.get("next_date"):
            continue
        left = db.days_until(r["next_date"])
        if r["type"] in allowed and 0 <= left <= 30:
            nxt = (today + timedelta(days=PLAN_CYCLE.get(r["type"], 30))).isoformat()
            items.append({"type": r["type"], "type_label": r["type_label"],
                          "date": r["next_date"], "title": f"{r['title']}（到期复做）",
                          "note": f"原记录安排于 {r['next_date']}，到期复做",
                          "next_date": nxt})
            seen.add(r["type"])

    # ② 超过默认周期未做的类型：3 天内补做一次
    last_by_type: dict[str, str] = {}
    for r in records:
        if r["date"] and (r["type"] not in last_by_type or r["date"] > last_by_type[r["type"]]):
            last_by_type[r["type"]] = r["date"]
    for t, cycle in PLAN_CYCLE.items():
        if t in seen or t not in allowed:
            continue
        last = last_by_type.get(t)
        if not last:
            continue  # 从未做过的类型不强行推送
        gap = (today - date.fromisoformat(last)).days
        if gap > cycle * 1.2:
            d = (today + timedelta(days=3)).isoformat()
            nxt = (today + timedelta(days=3 + cycle)).isoformat()
            items.append({"type": t, "type_label": db.RECORD_TYPES.get(t, t),
                          "date": d, "title": f"{db.RECORD_TYPES.get(t, t)}（超期补做）",
                          "note": f"距上次已 {gap} 天，超出常规 {cycle} 天周期",
                          "next_date": nxt})

    items.sort(key=lambda x: x["date"])
    # AI 总结评语（有 Key 才生成；失败静默降级）
    summary, mode = _plan_fallback_summary(), "example"
    if provider() and not _agent_failed and items:
        it_lines = "\n".join(f"- {x['date']}【{x['type_label']}】{x['title']}" for x in items)
        prompt = (f"宠物「{pet['name']}」的月度护理计划如下：\n{it_lines}\n"
                  "请写一段 60 字以内的引言：点出本月护理重点与排序理由，语气温暖克制，直接输出正文。")
        try:
            result = _ask_agent(prompt)
            if not result.startswith("⚠️"):
                summary, mode = result.strip(), "agent"
        except Exception:
            pass
    return {"pet_id": pet_id, "pet_name": pet["name"], "summary": summary,
            "mode": mode, "items": items}


# ---------------------------------------------------------------- 对外入口

def answer(message: str, history: list[dict] | None = None) -> dict:
    """分层多专家问答。返回 {"reply", "mode": "agent"|"example", "expert", "route", "degraded"?}。

    降级链：① 规则路由选专家 → ② 专家（供应商链内重试）→ ③ 通用 Agent（全工具）→ ④ 规则模式。
    mode 保持 agent/example 二值（前端与 /api/agent/status 依赖），专家名放 expert 字段。
    history：最近多轮对话 [{role, content}, ...]（时间正序），支持追问；规则模式为单轮。
    """
    expert, route_src = _route(message)
    if provider() and not _agent_failed:
        reply = _ask_with(expert, message, history)
        if not reply.startswith("⚠️"):
            return {"reply": reply, "mode": "agent", "expert": expert,
                    "expert_label": EXPERTS[expert]["label"], "route": route_src}
        if expert != "general_agent":
            reply = _ask_with("general_agent", message, history)
            if not reply.startswith("⚠️"):
                return {"reply": reply, "mode": "agent", "expert": "general_agent",
                        "expert_label": EXPERTS["general_agent"]["label"], "route": route_src, "degraded": True}
        return {"reply": reply + "，本条为示例回答。\n\n" + _example_answer(message),
                "mode": "example", "expert": "none", "route": route_src}
    return {"reply": _example_answer(message), "mode": "example", "expert": "none", "route": route_src}


def current_provider() -> str | None:
    """当前实际可用的供应商（已熔断的不算）。"""
    live = [p for p in _llm_candidates() if p not in _dead_providers]
    return live[0] if live else None


def status() -> dict:
    """供前端探测当前运行模式。"""
    prov = current_provider()
    return {"mode": "agent" if prov else "example",
            "has_key": bool(_llm_candidates()),
            "provider": prov}
