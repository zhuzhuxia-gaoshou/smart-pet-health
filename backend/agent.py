# -*- coding: utf-8 -*-
"""agent.py — LangChain Agent（DeepSeek 工具调用 / 通义千问 ReAct）+ 无 Key 降级"示例回答"模式。

- 配置 DEEPSEEK_API_KEY   → DeepSeek Chat（OpenAI 兼容端点，原生 function calling 驱动 ReAct 循环）
- 仅配置 DASHSCOPE_API_KEY → 通义千问 ChatTongyi（若不支持工具调用则自动降级）
- 都未配置                → ExampleAgent（解析关键词，调用同一套工具查数据库拼回答）

对外入口：answer(message) -> {"reply": str, "mode": "agent"|"example"}
"""
import hashlib
import json
import os
import re
import threading
import time

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
- query_medications(宠物名): 查该宠物的用药情况——在用药物的剂量/频次/疗程剩余天数与已结束的用药历史
- query_expenses(宠物名, 月份): 查养宠花费——合计/分类占比/按宠物分摊/最近明细；宠物名留空表示全部，月份传 YYYY-MM 或 YYYY，留空表示本月
- query_feeding(宠物名): 查该宠物的饮食日志——今日/本周喂食次数、近 30 天类型分布（干粮/湿粮/零食/生骨肉）与最近流水
- query_medbox(宠物名): 查家庭药箱库存——药品名/剂型/数量/有效期与开封后剩余天数、状态（可用/临期/已过期）与关联宠物；宠物名留空表示全部"""

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
# 门禁不变式：全系统唯一写路径仍是 create_record_draft 草稿 → 前端确认卡（工具本身不入库）。
# 三位专家中只有 care_advisor 拥有该工具；通用兜底 Agent 保留全工具集，保证降级后仍能起草。
# 禁止给任何专家注册直接写库的工具。

_MED_NOTE = "涉及医疗判断（是否生病、是否停药、指标是否异常）时，明确提示「请以兽医意见为准」。用简体中文，专业、温暖、克制。"

ANALYST_PROMPT = """你是「智能宠物健康管家」的【健康分析师】。你的职责：基于数据库真实数据，回答事实查询与状况评估类问题——宠物档案、健康记录（疫苗/体检/驱虫/喂药/就诊）、临期与逾期提醒、综合健康分析、用药情况、饮食日志（吃什么/吃多少/食欲变化）、多宠物的关注优先级、养宠花费与开销构成。

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
                                 "query_medications", "get_attention_ranking", "query_expenses", "query_feeding",
                                 "query_medbox"]},
    "care_advisor":   {"label": "护理顾问", "prompt": ADVISOR_PROMPT,
                       "tools": ["query_pet", "get_care_guide", "query_medications", "get_reminders",
                                 "create_record_draft"]},
    "report_writer":  {"label": "报告撰稿人", "prompt": WRITER_PROMPT,
                       "tools": ["generate_report", "query_pet", "query_health_records", "query_memories"]},
    "general_agent":  {"label": "通用助手", "prompt": SYSTEM_PROMPT, "tools": None},
}

# 路由规则（按优先级；建档意图永远最高，保证 main.py 的草稿兜底重试必中 care_advisor）
_ROUTE_DRAFT = re.compile(r"记一笔|记一下|记录一下|帮我记|帮我登记|登记一下|补充一条|添加一条记录|create_record_draft|起草")
_ROUTE_REPORT = re.compile(r"报告|周报|月报|年报|报表|总结|成长回顾|回忆|故事")
_ROUTE_CARE = re.compile(r"能吃|不能吃|可以吃|禁忌|该做|不该做|怎么照顾|照顾|护理|注意什么|怎么办|换羽|能不能|可不可以|注意事项")
# 「第一次」不再路由到报告撰稿人（"第一次吃生骨肉"是饮食陈述）：饮食场景词归健康分析师
_ROUTE_HEALTH = re.compile(r"疫苗|驱虫|体检|用药|吃药|什么药|药物|剂量|体重|健康|分析|记录|提醒|到期|临期|逾期|过期|优先|先管|就诊|复诊|三联|狂犬|打针|接种|花了|开销|多少钱|花费|记账|花销|支出|费用|账单|开支|喂了|喂食|喂过|在吃什么|吃了什么|最近吃|饮食|食欲|食量|吃得|生骨肉|第一次吃|第一次喂|药箱|药品库存|库存|开封|有效期")
# 弱信号（"怎么样/多大"等）单独出现太泛（"今天天气怎么样"），只在句中带库内宠物名时才算健康问题
_ROUTE_HEALTH_WEAK = re.compile(r"怎么样|状况|多大|多重|几岁|情况|正常吗")
_VACCINE_WORDS = re.compile(r"疫苗|驱虫|接种")


# ---- LLM 路由兜底分类（仅 default 分支触发；默认 LLM_ROUTE=0 关闭，见 docs/LLM路由兜底方案.md）----
_LLM_ROUTE_OFF = False        # 评估短路开关（eval --routing-only 置 True）
_LLM_ROUTE_FAILS = 0          # 连续失败计数
_LLM_ROUTE_FAIL_MAX = 3       # ≥3 次 → 本进程内停用 LLM 路由
_ROUTE_LLM_CACHE: dict = {}   # message -> label，FIFO 上限 128（进程内）

ROUTE_CLASSIFY_PROMPT = """你是宠物健康助手的意图分类器。把用户消息归入恰好一类，只输出对应的一个大写字母，禁止输出任何其他文字。
A：查询或评估事实数据——档案、健康记录（疫苗/体检/驱虫/喂药/就诊）、提醒到期、体重、用药、饮食日志、花费账单、症状观察与健康担忧（如"吐了一次要紧吗"）。
B：怎么做/怎么养——护理方法、能不能吃、禁忌、注意事项、用品与粮食选购建议。
C：要一份成文内容——健康报告、年度总结、成长回顾、纪念或介绍文案。
D：问候、闲聊、天气、与宠物无关的话题，或语义不明。
例：
「可乐这几天有点拉稀」→ A
「小狗疫苗间隔多久打一次？」→ A
「猫咪挑食怎么办」→ B
「给仓鼠买什么垫料？」→ B
「写一首关于我家狗狗的小诗」→ C
「谢谢啦」→ D
用户消息：{message}
字母："""


def _llm_route_enabled() -> bool:
    return (not _LLM_ROUTE_OFF) and _LLM_ROUTE_FAILS < _LLM_ROUTE_FAIL_MAX \
        and os.environ.get("LLM_ROUTE", "0") == "1"


def _classify_llm(prov: str):
    """分类专用轻量构建：temperature=0、max_tokens=512（qwen3.8-flash 思考块吃 token，64 实测间歇空输出）、超时 6s（冷启动实测 7s）、不重试。"""
    if prov == "bailian":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=os.environ.get("BAILIAN_MODEL", "qwen3.8-flash"),
                             api_key=bailian_key(),
                             base_url=os.environ.get("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/apps/anthropic"),
                             temperature=0, max_tokens=512, timeout=6, max_retries=0)
    if prov == "deepseek":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
                          api_key=deepseek_key(),
                          base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                          temperature=0, max_tokens=512, timeout=6, max_retries=0)
    from langchain_community.chat_models.tongyi import ChatTongyi
    return ChatTongyi(model=os.environ.get("QWEN_MODEL", "qwen-plus"),
                      dashscope_api_key=dashscope_key(),
                      temperature=0, max_tokens=512, request_timeout=6)


def _route_cache_put(msg: str, label: str) -> None:
    _ROUTE_LLM_CACHE.pop(msg, None)
    _ROUTE_LLM_CACHE[msg] = label
    while len(_ROUTE_LLM_CACHE) > 128:
        _ROUTE_LLM_CACHE.pop(next(iter(_ROUTE_LLM_CACHE)))


def _llm_classify(message: str) -> str | None:
    """返回 health_analyst/care_advisor/report_writer/general；任何失败返回 None（回落 general）。"""
    global _LLM_ROUTE_FAILS
    msg = message.strip()[:200]
    if not msg:
        return None
    if msg in _ROUTE_LLM_CACHE:
        return _ROUTE_LLM_CACHE[msg]
    if current_provider() is None or _agent_failed:
        return None
    mapping = {"A": "health_analyst", "B": "care_advisor", "C": "report_writer", "D": "general"}
    for prov in _llm_candidates():
        if _provider_skippable(prov):
            continue
        try:
            content = ""
            # qwen3.8-flash 间歇把 token 花在思考块上导致 text 为空：max_tokens 放宽到 512，空输出同供应商重试一次
            for _attempt in range(2):
                out = _classify_llm(prov).invoke([("user", ROUTE_CLASSIFY_PROMPT.format(message=msg))])
                content = out.content
                if isinstance(content, list):
                    content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
                if (content or "").strip():
                    break
            m = re.search(r"[ABCD]", (content or "").strip().upper())
            if not m:
                raise ValueError(f"非法分类输出: {(content or '')[:20]!r}")
            label = mapping[m.group(0)]
            _LLM_ROUTE_FAILS = 0
            _route_cache_put(msg, label)
            return label
        except Exception as e:
            txt = str(e)
            if "402" in txt or "Insufficient Balance" in txt or "Arrearage" in txt:
                _trip_provider(prov)
            continue
    _LLM_ROUTE_FAILS += 1
    if _LLM_ROUTE_FAILS >= _LLM_ROUTE_FAIL_MAX:
        print(f"[agent] LLM 路由分类连续失败 {_LLM_ROUTE_FAILS} 次，本进程内停用")
    return None


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
    # 规则未命中 → LLM 分类兜底（默认关闭，LLM_ROUTE=1 启用）；任何失败回落 general，与历史行为一致
    if _llm_route_enabled():
        label = _llm_classify(msg)
        if label:
            return ("general_agent" if label == "general" else label), f"llm:{label}"
    return "general_agent", "default"

# ---------------------------------------------------------------- LangChain Agent

_agent_cache: dict = {}          # 按 provider 缓存已构建的 Agent
_dead_providers: set = set()     # 欠费等持续性错误 → 本进程内熔断该供应商
_dead_since: dict = {}           # 各供应商熔断发生时刻（time.time()），供半开恢复判断
_HALF_OPEN_SECS = 300            # 熔断满 5 分钟进入半开：允许再试（如 DeepSeek 充值后免重启）
_agent_failed = False


def _provider_skippable(prov: str) -> bool:
    """熔断中且未到半开窗口的供应商跳过；满 _HALF_OPEN_SECS 秒允许再试一次。"""
    return prov in _dead_providers and (time.time() - _dead_since.get(prov, 0.0)) < _HALF_OPEN_SECS


def _trip_provider(prov: str) -> None:
    """熔断供应商并记录时刻（重复熔断重新计时）。"""
    _dead_providers.add(prov)
    _dead_since[prov] = time.time()


def _revive_provider(prov: str) -> None:
    """半开尝试成功 → 彻底恢复该供应商。"""
    _dead_providers.discard(prov)
    _dead_since.pop(prov, None)


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

    class ExpenseQueryIn(BaseModel):
        name: str = Field(default="", description="宠物名字；留空表示全部宠物（含家庭共同支出）")
        month: str = Field(default="", description="YYYY-MM 查某月、YYYY 查全年；留空表示本月。'今年'换算为当前年份")

    class MedboxQueryIn(BaseModel):
        name: str = Field(default="", description="宠物名字；留空表示查看家庭全部药箱药品")

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
        StructuredTool.from_function(
            lambda name="", month="": tools.query_expenses(name, month),
            name="query_expenses",
            description=tools.query_expenses.__doc__.strip(),
            args_schema=ExpenseQueryIn),
        StructuredTool.from_function(tools.query_feeding, name="query_feeding",
                                     description=tools.query_feeding.__doc__.strip(),
                                     args_schema=NameIn),
        StructuredTool.from_function(
            lambda name="": tools.query_medbox(name),
            name="query_medbox",
            description=tools.query_medbox.__doc__.strip(),
            args_schema=MedboxQueryIn),
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
        if _provider_skippable(prov):
            continue
        try:
            agent = _get_agent(prov, profile)
            result = agent.invoke({"messages": msgs}, config={"recursion_limit": 12})
            text = _extract_ai_text(result)
            if text:
                _revive_provider(prov)   # 半开重试成功 → 彻底恢复
                return text
            last_err = RuntimeError("模型未返回内容")
        except Exception as e:
            last_err = e
            text = str(e)
            if "402" in text or "Insufficient Balance" in text or "Arrearage" in text:
                _trip_provider(prov)  # 欠费是持续状态 → 熔断，半开窗口后自动重试
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
    expense_kw = any(k in msg for k in ("花了", "开销", "多少钱", "花费", "记账", "花销", "支出", "费用", "账单", "开支"))
    feeding_kw = any(k in msg for k in ("喂了", "喂食", "喂过", "在吃什么", "吃了什么", "最近吃", "饮食", "食欲", "食量", "吃得"))

    # 「在吃什么药」「最近吃药了吗」含"药"字属用药场景，让位给下方 med 分支；
    # 不能用 med_kw 判定（其词"在吃"是"在吃什么"的子串，会误伤纯饮食问题）
    if pet and feeding_kw and not care_kw and "药" not in msg:
        return tools.query_feeding(pet) + "\n\n> 当前为示例回答模式（未配置 API Key）。"

    if expense_kw:
        import re as _re
        from datetime import date as _date
        ym = _re.search(r"(20\d{2})[年\-/](\d{1,2})", msg)     # 2026年9 / 2026-09
        yr = _re.search(r"(20\d{2})\s*年", msg)                # 2026年（全年）
        if ym:
            month_arg = f"{ym.group(1)}-{int(ym.group(2)):02d}"
        elif yr:
            month_arg = yr.group(1)
        elif "今年" in msg:
            month_arg = str(_date.today().year)
        else:
            month_arg = ""                                     # 本月
        return tools.query_expenses(pet or "", month_arg) + "\n\n> 当前为示例回答模式（未配置 API Key）。"

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
    "5) 调用 query_expenses（宠物名与月份都留空）用一句话提本月花销概览：合计与最大开销类别，没有记账则跳过；"
    "6) 收尾一句温暖克制的总结。直接输出简报正文，不要大标题。"
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
    from datetime import date as _date
    today = _date.today()
    exp = db.expense_summary(today.year, today.month)
    if exp["count"]:
        cats = sorted(exp["by_category"].items(), key=lambda kv: -kv[1])
        top = db.EXPENSE_CATEGORIES.get(cats[0][0], cats[0][0]) if cats else ""
        lines.append(f"本月已记 {exp['count']} 笔花销，合计 ¥{exp['total']:.2f}"
                     + (f"，{top}占比最高。" if top else "。"))
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
    feed = db.feeding_summary(pet_id)
    feed_total = sum(feed["by_type_30d"].values())
    # 签名纳入近 30 天饮食条数：新增饮食记录后解读随之刷新
    sig = f"{len(weights)}|{weights[-1]['weight']}|{weights[-1]['date']}|{records[0]['id'] if records else 0}|{feed_total}"
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
        f_line = ("、".join(f"{db.FEEDING_TYPES.get(k, k)}{v}次" for k, v in
                           sorted(feed["by_type_30d"].items(), key=lambda kv: -kv[1]))
                  + f"（本周 {feed['week']} 次）") if feed_total else "暂无记录"
        prompt = (f"请对宠物「{pet['name']}」（{species.type_label(pet['type'])}）的体重趋势给出 80 字以内的解读，"
                  "内容包括：变化方向与幅度是否合理、可能原因（结合饮食结构，如零食/生骨肉比例）、一条可执行的建议。直接输出解读正文。\n"
                  f"体重记录：\n{w_lines}\n近期健康记录：\n{r_lines}\n近 30 天饮食：{f_line}")
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


# ---------------------------------------------------------------- 药箱：OCR 识药与管家简报

MEDBOX_OCR_PROMPT = (
    "这是宠物药品照片。识别药名/剂型(药片tablet胶囊capsule口服液liquid滴剂drops外用ointment喷剂"
    "spray针剂injection其他other)/规格/数量单位/有效期/用途，只输出JSON：{\"items\":[{\"name\":\"…\","
    "\"form\":\"tablet\",\"spec\":\"…\",\"qty\":2,\"unit\":\"盒\",\"expiry_date\":\"YYYY-MM-DD\","
    "\"purpose\":\"…\"}]}。一张药盒输出 1 条，处方单可输出多条；识别不出的字段直接省略，日期一律"
    " YYYY-MM-DD；form 只取那 8 个英文键之一。除 JSON 外不要输出任何其他文字。"
)
_OCR_ERR_TEXT = "未能识别，可改用文字描述或手动填写"


def _extract_text(content) -> str:
    """LangChain 消息 content 归一为纯文本：Anthropic 块数组只取 text 块（忽略 thinking）。"""
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
    return str(content or "")


def medbox_ocr(image_data_uri: str) -> dict:
    """视觉识药：药盒/处方单照片 → 条目候选（不落库，前端确认后走 POST /api/medbox）。
    按供应商链（百炼 → DeepSeek，跳过熔断中者）以多模态消息调用；只输出 JSON。
    任何失败——无可用视觉供应商/无输出/坏 JSON/清洗后无有效条目——统一返回 {error}。"""
    import db
    import datetime as _dt
    for prov in _llm_candidates():
        if _provider_skippable(prov) or prov == "tongyi":
            continue          # 通义走 DashScope 原生 SDK，多模态消息格式不同，本工具不支持
        try:
            if prov == "bailian":
                from langchain_anthropic import ChatAnthropic
                model = ChatAnthropic(model=os.environ.get("BAILIAN_MODEL", "qwen3.8-flash"),
                                      api_key=bailian_key(),
                                      base_url=os.environ.get("BAILIAN_BASE_URL",
                                                              "https://dashscope.aliyuncs.com/apps/anthropic"),
                                      temperature=0, max_tokens=1024, timeout=30, max_retries=0)
            else:
                from langchain_openai import ChatOpenAI
                model = ChatOpenAI(model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
                                   api_key=deepseek_key(),
                                   base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                                   temperature=0, max_tokens=1024, timeout=30, max_retries=0)
            out = model.invoke([{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": image_data_uri}},
                {"type": "text", "text": MEDBOX_OCR_PROMPT}]}])
            text = _extract_text(out.content)
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                continue
            data = json.loads(m.group(0))
            cleaned = []
            for raw in data.get("items") or []:
                if not isinstance(raw, dict):
                    continue
                item_name = (str(raw.get("name") or "")).strip()[:60]
                if not item_name:
                    continue      # 无名条目整条丢弃
                item: dict = {"name": item_name}
                form = str(raw.get("form") or "").strip().lower()
                item["form"] = form if form in db.MED_FORMS else "other"
                if raw.get("spec"):
                    item["spec"] = str(raw["spec"]).strip()[:200]
                if raw.get("qty") is not None:
                    try:
                        q = float(raw["qty"])
                        if q >= 0:
                            item["qty"] = q
                    except (TypeError, ValueError):
                        pass      # 数字非法丢字段
                if raw.get("unit"):
                    item["unit"] = str(raw["unit"]).strip()[:20]
                for key in ("expiry_date", "opened_date"):
                    v = str(raw.get(key) or "").strip()
                    if v:
                        try:
                            _dt.datetime.strptime(v, "%Y-%m-%d")
                            item[key] = v
                        except ValueError:
                            pass  # 日期非法丢字段
                if raw.get("purpose"):
                    item["purpose"] = str(raw["purpose"]).strip()[:200]
                cleaned.append(item)
            if cleaned:
                _revive_provider(prov)
                return {"items": cleaned}
        except Exception as e:
            txt = str(e)
            if "402" in txt or "Insufficient Balance" in txt or "Arrearage" in txt:
                _trip_provider(prov)
            continue
    return {"error": _OCR_ERR_TEXT}


def medbox_briefing_fallback() -> str:
    """无 Key / LLM 失败时的规则版药箱话术（数据来自真实库）。"""
    import db
    items = db.list_medbox()
    if not items:
        return "药箱还是空的，可以在「药箱」页面录入第一件常备药。"
    attention = [i for i in items if i["status"] != "ok"]
    lines = [f"药箱共 {len(items)} 种药品。"]
    if attention:
        parts = []
        for a in attention[:4]:
            parts.append(f"{a['name']}（已过期）" if a["remain_days"] is not None and a["remain_days"] < 0
                         else f"{a['name']}（剩 {a['remain_days']} 天）")
        lines.append("需要关注：" + "、".join(parts) + "。")
        if len(attention) > 4:
            lines.append(f"另有 {len(attention) - 4} 种也需留意。")
    else:
        lines.append("全部在有效期内，一切安好。")
    return "\n".join(lines)


def _medbox_briefing_llm(prompt: str) -> str:
    """简报专用直答：无工具纯文本（复用 _ask_agent 风格，但独立小函数、超时 30s）。
    全链失败返回以 ⚠️ 开头的说明文本（调用方据此回落规则版）。"""
    last_err = None
    for prov in _llm_candidates():
        if _provider_skippable(prov):
            continue
        try:
            if prov == "bailian":
                from langchain_anthropic import ChatAnthropic
                model = ChatAnthropic(model=os.environ.get("BAILIAN_MODEL", "qwen3.8-flash"),
                                      api_key=bailian_key(),
                                      base_url=os.environ.get("BAILIAN_BASE_URL",
                                                              "https://dashscope.aliyuncs.com/apps/anthropic"),
                                      temperature=0.3, max_tokens=4096, timeout=60, max_retries=1)
            elif prov == "deepseek":
                from langchain_openai import ChatOpenAI
                model = ChatOpenAI(model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
                                   api_key=deepseek_key(),
                                   base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                                   temperature=0.3, max_tokens=4096, timeout=60)
            else:
                from langchain_community.chat_models.tongyi import ChatTongyi
                model = ChatTongyi(model=os.environ.get("QWEN_MODEL", "qwen-plus"),
                                   dashscope_api_key=dashscope_key(),
                                   temperature=0.3, request_timeout=60)
            text = ""
            for _attempt in range(2):        # 思考模型偶发空输出：同供应商重试一次（与巡检链路同款加固）
                out = model.invoke([("user", prompt)])
                text = _extract_text(out.content).strip()
                if text:
                    break
            if text:
                _revive_provider(prov)
                return text
            last_err = RuntimeError("模型未返回内容")
        except Exception as e:
            last_err = e
            txt = str(e)
            if "402" in txt or "Insufficient Balance" in txt or "Arrearage" in txt:
                _trip_provider(prov)
    if last_err is not None:
        return f"⚠️ 药箱简报调用失败（{type(last_err).__name__}: {last_err}）"
    return "⚠️ 没有可用的模型供应商"


def medbox_briefing() -> dict:
    """药箱管家话：有 Key 走 LLM 总结+建议，失败/无 Key 回落规则拼接；返回 {text, mode, cached}。
    按数据签名（条数|关注数|各条剩余天数|今天）缓存 app_kv，命中直返 cached；
    同一天内药箱不变不重复调 LLM（简报成本护栏，日期入签名保证每日刷新）。"""
    import db
    from datetime import date as _date
    items = db.list_medbox()
    attention = [i for i in items if i["status"] != "ok"]
    today = _date.today().isoformat()
    sig = (f"{len(items)}|{len(attention)}|"
           + ",".join(str(i["remain_days"]) for i in items) + f"|{today}")
    key = "medbox:briefing"
    meta = db.kv_get_meta(key)
    if meta:
        try:
            data = json.loads(meta["value"])
            if data.get("sig") == sig:
                return {"text": data["text"], "mode": data.get("mode", "example"),
                        "cached": True, "generated_at": meta["updated_at"]}
        except Exception:
            pass
    text, mode = medbox_briefing_fallback(), "example"
    if items and provider() and not _agent_failed:
        facts = [f"共 {len(items)} 种药品，需要关注（过期/临期）{len(attention)} 种。"]
        for a in attention[:8]:
            facts.append(f"- {a['name']}（{a['form_label']}）：{a['status_label']}，截止 {a['effective_deadline']}"
                         + (f"，剩 {a['remain_days']} 天" if a["remain_days"] is not None else ""))
        if not attention:
            facts.append("- 其余全部在有效期内。")
        prompt = ("你是宠物健康管家的药箱管家。以下是药箱现状：\n" + "\n".join(facts)
                  + f"\n[系统注：今天是 {today}]\n"
                  "用不超过80字中文总结药箱状态并给一条最优先行动建议，"
                  "涉及用药判断以兽医意见为准，不要编造数字，直接输出正文。")
        try:
            result = _medbox_briefing_llm(prompt)
            if not result.startswith("⚠️"):
                text, mode = result.strip(), "agent"
        except Exception:
            pass
    db.kv_set(key, json.dumps({"text": text, "mode": mode, "sig": sig}, ensure_ascii=False))
    return {"text": text, "mode": mode, "cached": False}


# ---------------------------------------------------------------- AI 巡检员
# 分工：db.patrol_scan() 规则引擎出事实与通知资格；LLM 只做表达润色（一次批量调用）。
# 诚信护栏：润色文本里的每个数字都必须能在该条 title/facts 中找到，否则整条丢弃。
# 成本护栏：sig 缓存当天命中零成本；stale 才后台润色；每日预算 2 次（自动1+手动1）；
#           patrol:fail:<date> 连续失败 2 次当日停用；PATROL_LLM=0 硬开关永远模板态。

PATROL_CACHE_KEY = "patrol:cache"
PATROL_HISTORY_KEY = "patrol:history"
PATROL_BUDGET_MAX = 2          # 每日润色预算（自动 1 + 手动 1）
PATROL_FAIL_MAX = 2            # 当日连续润色失败次数达到即停用
_patrol_lock = threading.Lock()
_patrol_generating = False     # 并发票：同时最多一个后台润色任务


def _patrol_today() -> str:
    from datetime import date as _date
    return _date.today().isoformat()


def _patrol_kv_int(key: str) -> int:
    import db
    try:
        return int(db.kv_get(key) or "0")
    except (TypeError, ValueError):
        return 0


def _patrol_sig(findings: list) -> str:
    """事实签名：只装 id/level/title（LLM 文案不进签名），排序后 sha1。"""
    base = "|".join(sorted(f"{f['id']}:{f['level']}:{f['title']}" for f in findings))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def patrol_briefing_fallback_text(findings: list) -> str:
    """日报模板句（无 LLM 也能写 history text）。"""
    if not findings:
        return "6 项信号已核对，今天一切平稳。"
    return "今日巡检 " + str(len(findings)) + " 项发现：" + "；".join(f["title"] for f in findings[:6]) + "。"


def _patrol_llm(prompt: str) -> str:
    """巡检润色专用直答（text-only）。max_tokens=4096：qwen3.8-flash 思考块吃掉大量预算，
    1024 实测整段被 thinking 耗尽返回空 text；空输出同供应商重试一次（同 _classify_llm 经验）。
    全链失败返回以 ⚠️ 开头的说明文本（调用方计失败）。"""
    last_err = None
    for prov in _llm_candidates():
        if _provider_skippable(prov):
            continue
        try:
            if prov == "bailian":
                from langchain_anthropic import ChatAnthropic
                model = ChatAnthropic(model=os.environ.get("BAILIAN_MODEL", "qwen3.8-flash"),
                                      api_key=bailian_key(),
                                      base_url=os.environ.get("BAILIAN_BASE_URL",
                                                              "https://dashscope.aliyuncs.com/apps/anthropic"),
                                      temperature=0.3, max_tokens=4096, timeout=60, max_retries=0)
            elif prov == "deepseek":
                from langchain_openai import ChatOpenAI
                model = ChatOpenAI(model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
                                   api_key=deepseek_key(),
                                   base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                                   temperature=0.3, max_tokens=4096, timeout=60)
            else:
                from langchain_community.chat_models.tongyi import ChatTongyi
                model = ChatTongyi(model=os.environ.get("QWEN_MODEL", "qwen-plus"),
                                   dashscope_api_key=dashscope_key(),
                                   temperature=0.3, request_timeout=60)
            text = ""
            for _attempt in range(2):        # 思考模型偶发整段空输出：同供应商重试一次
                out = model.invoke([("user", prompt)])
                text = _extract_text(out.content).strip()
                if text:
                    break
            if text:
                _revive_provider(prov)
                return text
            last_err = RuntimeError("模型未返回内容")
        except Exception as e:
            last_err = e
            txt = str(e)
            if "402" in txt or "Insufficient Balance" in txt or "Arrearage" in txt:
                _trip_provider(prov)
    if last_err is not None:
        return f"⚠️ 巡检润色调用失败（{type(last_err).__name__}: {last_err}）"
    return "⚠️ 没有可用的模型供应商"


PATROL_PROMPT_SYSTEM = (
    "你是宠物健康管家。为每条巡检发现各写一句 ≤40字中文建议，语气专业温暖克制。"
    "只能使用输入事实中的数字，严禁新增或改写任何数字。医疗判断提示以兽医意见为准。"
    '只输出 JSON：{"prose":[{"finding_id":"…","text":"…"}],'
    '"insight":{"text":"≤60字跨条目综合观察，没有值得关联的就输出空串"}}'
)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _patrol_fact_numbers(finding: dict) -> set:
    """一条发现的可信数字集合（title + facts 的 k/v）。"""
    parts = [finding.get("title") or ""]
    parts += [str(p.get("k", "")) + str(p.get("v", "")) for p in finding.get("facts") or []]
    return set(_NUM_RE.findall(" ".join(parts)))


def _patrol_validate(out: dict, findings: list) -> tuple[dict, str]:
    """润色结果逐条校验（纯函数，可单测）：
    finding_id 必须存在于输入；text 中每个数字都必须出现在该条 title/facts 数字集合里；
    insight.text 的数字必须出现在全部发现的数字并集里。不合格条目丢弃，不抛异常。"""
    prose: dict[str, str] = {}
    by_id = {f["id"]: f for f in findings}
    all_nums: set = set()
    for f in findings:
        all_nums |= _patrol_fact_numbers(f)
    for item in (out.get("prose") or []):
        try:
            fid = str(item.get("finding_id") or "")
            text = str(item.get("text") or "").strip()
            if not fid or fid not in by_id or not text:
                continue
            allowed = _patrol_fact_numbers(by_id[fid])
            if any(n not in allowed for n in _NUM_RE.findall(text)):
                continue          # 编造/改写数字 → 丢弃该条
            prose[fid] = text[:120]
        except (AttributeError, TypeError):
            continue
    insight_text = ""
    ins = out.get("insight")
    if isinstance(ins, dict):
        insight_text = str(ins.get("text") or "").strip()[:160]
        if insight_text and any(n not in all_nums for n in _NUM_RE.findall(insight_text)):
            insight_text = ""    # 综合观察数字越界 → 整条丢
    return prose, insight_text


def patrol_polish(findings: list, force: bool = False) -> dict:
    """一次批量润色：规则发现 → prose 映射 + 跨条目综合观察。
    调用失败/JSON 不可解析 → {"ok": False}（当日连续 2 次失败后停用；force=手动刷新旁路停用）。
    环境性失败（链灭/欠费）不计入停用计数——充值恢复后必须留自愈路径。
    永不同步阻塞请求线程之外的逻辑——由 patrol_report 的后台线程调用。"""
    import db
    if not findings:
        return {"ok": True, "prose": {}, "insight": ""}
    today = _patrol_today()
    if not force and _patrol_kv_int(f"patrol:fail:{today}") >= PATROL_FAIL_MAX:
        return {"ok": False, "disabled": True}
    lines = [PATROL_PROMPT_SYSTEM, f"\n[系统注：今天是 {today}]\n巡检发现："]
    for f in findings:
        facts = "，".join(f"{p['k']} {p['v']}" for p in f.get("facts") or [])
        lines.append(f'- finding_id={f["id"]}｜{f["title"]}｜{facts}')
    raw = _patrol_llm("\n".join(lines))
    if raw.startswith("⚠️"):
        env_down = ("没有可用" in raw or any(k in raw for k in ("402", "Insufficient Balance", "Arrearage")))
        if not env_down:   # 环境灭不算质量坏：不计数，恢复后可重试
            db.kv_set(f"patrol:fail:{today}", str(_patrol_kv_int(f"patrol:fail:{today}") + 1))
        return {"ok": False, "env": env_down}   # env=请求根本没发出去，预算应退款
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        db.kv_set(f"patrol:fail:{today}", str(_patrol_kv_int(f"patrol:fail:{today}") + 1))
        return {"ok": False}
    try:
        out = json.loads(m.group(0))
    except Exception:
        db.kv_set(f"patrol:fail:{today}", str(_patrol_kv_int(f"patrol:fail:{today}") + 1))
        return {"ok": False}
    if not isinstance(out, dict):
        return {"ok": False}
    prose, insight = _patrol_validate(out, findings)
    db.kv_set(f"patrol:fail:{today}", "0")   # 成功清连续失败计数
    return {"ok": True, "prose": prose, "insight": insight}


def _patrol_top_level(findings: list) -> str:
    if any(f["level"] == "high" for f in findings):
        return "high"
    return "watch" if findings else "calm"


def _patrol_history_update(findings: list, text: str) -> None:
    """当日条目并入历史（最多 14 条）：润色成功或当天首次模板生成时调用。"""
    import db
    today = _patrol_today()
    try:
        hist = json.loads(db.kv_get(PATROL_HISTORY_KEY) or "[]")
        if not isinstance(hist, list):
            hist = []
    except Exception:
        hist = []
    counts = {"high": 0, "warn": 0, "info": 0}
    for f in findings:
        if f["level"] in counts:
            counts[f["level"]] += 1
    entry = {"date": today, "level": _patrol_top_level(findings), "counts": counts, "text": text}
    hist = [h for h in hist if isinstance(h, dict) and h.get("date") != today]
    hist.append(entry)
    db.kv_set(PATROL_HISTORY_KEY, json.dumps(hist[-14:], ensure_ascii=False))


def _patrol_claim_budget() -> bool:
    """原子认领当日润色预算（先 +1 再调用：认领-生成-回写）。"""
    import db
    key = f"patrol:budget:{_patrol_today()}"
    used = _patrol_kv_int(key)
    if used >= PATROL_BUDGET_MAX:
        return False
    db.kv_set(key, str(used + 1))
    return True


def _patrol_cache_load() -> dict | None:
    import db
    raw = db.kv_get(PATROL_CACHE_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _patrol_fill(findings: list, prose_map: dict, insight: dict | None,
                 mode: str, generated_at: str | None, fresh: bool) -> dict:
    """组装 patrol 响应（findings 各条填 ai_text；缺失回落 title）。"""
    import db
    out_findings = []
    for f in findings:
        g = dict(f)
        g["ai_text"] = prose_map.get(f["id"]) or f["title"]
        out_findings.append(g)
    insights = []
    if insight and insight.get("text"):
        insights.append({"id": "insight:0:" + _patrol_today(), "title": "今日综合观察",
                         "ai_text": insight["text"], "level": "info",
                         "facts": [], "link": None,
                         "mode": insight.get("mode", mode)})
    return {"level": _patrol_top_level(findings), "findings": out_findings,
            "insights": insights, "mode": mode, "generated_at": generated_at,
            "fresh": fresh, "scanned": len(db.PATROL_RULES),
            "history": _patrol_history()}


def _patrol_history() -> list:
    import db
    try:
        hist = json.loads(db.kv_get(PATROL_HISTORY_KEY) or "[]")
        return hist if isinstance(hist, list) else []
    except Exception:
        return []


def _patrol_bg_polish(findings: list, sig: str, force: bool = False) -> None:
    """后台线程：润色 → 回写缓存与历史；无论成败都放票。
    环境性失败（请求没发出去，零 token 成本）退回预算——充值/并发空出后当天仍有自愈额度。"""
    import db
    global _patrol_generating
    try:
        result = patrol_polish(findings, force=force)
        if result.get("ok"):
            today = _patrol_today()
            db.kv_set(PATROL_CACHE_KEY, json.dumps({
                "date": today, "sig": sig,
                "prose_map": result["prose"],
                "insight": {"text": result["insight"], "mode": "agent"},
                "mode": "agent" if result["prose"] or result["insight"] else "rules",
            }, ensure_ascii=False))
            hist_text = "；".join(list(result["prose"].values())[:3]) if result["prose"] else patrol_briefing_fallback_text(findings)
            _patrol_history_update(findings, hist_text)
        elif result.get("env"):
            bkey = f"patrol:budget:{_patrol_today()}"
            db.kv_set(bkey, str(max(0, _patrol_kv_int(bkey) - 1)))   # 退款
    except Exception:
        pass
    finally:
        with _patrol_lock:
            _patrol_generating = False


def _patrol_live() -> list:
    """活供应商（排除熔断未到期者）：链灭时不占票不占预算——给充值/并发恢复留全额自愈额度。"""
    return [p for p in _llm_candidates() if not _provider_skippable(p)]


def _patrol_try_spawn(findings: list, sig: str) -> None:
    """票与预算判定后才发后台线程（同时最多一个润色任务；每日预算封顶）。"""
    global _patrol_generating
    if os.environ.get("PATROL_LLM", "1") == "0" or not findings:
        return
    if not _patrol_live():
        return
    with _patrol_lock:
        if _patrol_generating:
            return
        _patrol_generating = True
    if not _patrol_claim_budget():
        with _patrol_lock:
            _patrol_generating = False
        return
    threading.Thread(target=_patrol_bg_polish, args=(findings, sig), daemon=True).start()


def patrol_report(force: bool = False) -> dict:
    """GET /api/patrol 核心：规则层永远实时，LLM 只在后台异步润色，本函数永不同步调 LLM。"""
    import db
    from datetime import datetime as _dt
    scan = db.patrol_scan()
    findings = scan["findings"]
    sig = _patrol_sig(findings)
    today = _patrol_today()
    cache = _patrol_cache_load()
    if cache and cache.get("date") == today and cache.get("sig") == sig:
        meta = db.kv_get_meta(PATROL_CACHE_KEY)
        return _patrol_fill(findings, cache.get("prose_map") or {}, cache.get("insight"),
                            cache.get("mode", "agent"), (meta or {}).get("updated_at"), True)
    # stale：本轮先回模板态；票/预算判定后才发后台线程（force=True 时留给 refresh 端点处理）
    if not force:
        _patrol_try_spawn(findings, sig)
    # 当天首次（或缓存过期/跨天）：模板版也入历史与缓存，保证 generated_at 与后续命中有锚点
    if not cache or cache.get("date") != today:
        _patrol_history_update(findings, patrol_briefing_fallback_text(findings))
        db.kv_set(PATROL_CACHE_KEY, json.dumps({
            "date": today, "sig": sig, "prose_map": {},
            "insight": {"text": "", "mode": "rules"}, "mode": "rules",
        }, ensure_ascii=False))
    return _patrol_fill(findings, {}, None, "rules", _dt.now().strftime("%Y-%m-%d %H:%M:%S"), False)


def patrol_manual_refresh() -> dict:
    """POST /api/patrol/refresh 的核心：sig 未变直接报 fresh；变了则占票+占预算后台重润色；预算满返回 capped。"""
    import db
    scan = db.patrol_scan()
    findings = scan["findings"]
    sig = _patrol_sig(findings)
    cache = _patrol_cache_load()
    today = _patrol_today()
    # 模板态缓存（如熔断期生成）不算"已润色"：手动刷新必须给重试机会，否则充值恢复后当天无自愈路径
    if cache and cache.get("date") == today and cache.get("sig") == sig and cache.get("mode") == "agent":
        return {"ok": True, "capped": False, "fresh": True}
    if os.environ.get("PATROL_LLM", "1") == "0" or not findings or not _patrol_live():
        return {"ok": True, "capped": False}   # 链灭/PATROL_LLM=0：不占票不占预算，模板态照常
    global _patrol_generating
    with _patrol_lock:
        if _patrol_generating:
            return {"ok": True, "capped": False}    # 已有任务在跑，静默合并
        _patrol_generating = True
    if not _patrol_claim_budget():
        with _patrol_lock:
            _patrol_generating = False
        return {"ok": True, "capped": True}     # 今日本额度用完：前端如实提示"明天自动恢复"
    threading.Thread(target=_patrol_bg_polish, args=(findings, sig, True), daemon=True).start()
    return {"ok": True, "capped": False}


# ---------------------------------------------------------------- 宠物收藏卡：AI 称号与赠言

CARD_TEXT_PROMPT = (
    "给这只宠物的收藏卡写一个称号（title，≤10字）和一句赠言（motto，≤20字）。"
    "用克制的游戏卡牌语言，不油腻不堆形容词，只输出 JSON：{{\"title\":\"…\",\"motto\":\"…\"}}。"
    "宠物事实：名字「{name}」，{who}，陪伴 {days} 天，稀有度 {rar}（{rar_label}），性格备注：{personality}。"
    "赠言可以提及陪伴，但不得编造输入之外的事实。"
)
CARD_RAR_LABEL = {"R": "新锐", "SR": "忠实", "SSR": "老牌", "UR": "传奇"}


def pet_card_text(pet: dict, days, rar: str, force: bool = False) -> dict | None:
    """收藏卡称号+赠言（打开卡时调用）。缓存按 宠物+稀有度 分档，升档自动重算。
    LLM 不可用/失败一律返回 None，前端回落模板文案——表达层永不阻塞主链路。"""
    import db
    if not provider() or _agent_failed or os.environ.get("PATROL_LLM", "1") == "0":
        return None
    key = f"card-text:{pet['id']}:{rar}"
    if not force:
        hit = db.kv_get(key)
        if hit:
            try:
                return json.loads(hit)
            except Exception:
                pass
    who = {"cat": "九命深思者", "dog": "阳光守护者", "bird": "羽翼歌唱家", "fish": "静默禅者"}.get(pet.get("type"), "独一无二的伙伴")
    prompt = CARD_TEXT_PROMPT.format(name=pet.get("name") or "小家伙", who=who,
                                     days=days if days is not None else "未知",
                                     rar=rar, rar_label=CARD_RAR_LABEL.get(rar, ""),
                                     personality=(pet.get("personality") or "无备注")[:60])
    out = _medbox_briefing_llm(prompt)      # 复用无工具直答小函数：超时 30s、402 熔断、链灭返回 ⚠️
    if out.startswith("⚠️"):
        return None
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    title, motto = str(d.get("title") or "").strip()[:16], str(d.get("motto") or "").strip()[:30]
    if not title or not motto:
        return None
    res = {"title": title, "motto": motto}
    db.kv_set(key, json.dumps(res, ensure_ascii=False))
    return res


# ---------------------------------------------------------------- 主人养成教练周报
# 契约：分数/任务/维度全部来自 db.coach_weekly()，LLM 只写口吻段落，禁止改写数字。
# 无 Key / 失败回落规则模板；COACH_LLM=0 硬开关。

COACH_CACHE_KEY = "coach:letter"


def coach_letter_fallback(payload: dict) -> str:
    score, grade = payload.get("score"), payload.get("grade")
    dims = payload.get("dims") or []
    weak = [d for d in dims if d.get("score", 100) < 85][:2]
    strong = [d for d in dims if d.get("score", 0) >= 95][:2]
    lines = [f"本周养育分 {score} 分（{grade}）。"]
    if strong:
        lines.append("做得好的：" + "、".join(d["label"] for d in strong) + "。")
    if weak:
        lines.append("还差一点：" + "、".join(f"{d['label']}（{d['note'] or d['score']}）" for d in weak) + "。")
    tasks = [t for t in (payload.get("tasks") or []) if t.get("status") == "open"]
    if tasks:
        lines.append("本周任务：" + "；".join(t["title"] for t in tasks[:3]) + "。")
    if payload.get("streak"):
        lines.append(f"连续达标 {payload['streak']} 周，稳住。")
    lines.append("习惯是慢慢养出来的，不求完美，只求本周比上周顺一点。")
    return "\n".join(lines)


def _coach_letter_llm(prompt: str) -> str:
    """无工具直答，风格同 medbox 简报；失败返回 ⚠️ 开头文本。"""
    return _medbox_briefing_llm(prompt)


_coach_letter_lock = threading.Lock()
_coach_letter_busy = False


def _coach_letter_prompt(payload: dict) -> str:
    return (
        "你是温柔但严格的宠物养育教练。根据下列「已锁定事实」写一封 120 字以内的周报口吻短文：\n"
        "1) 先具体夸 1 个做得好的维度；2) 再温和点出 1–2 个缺口（可引用 note）；"
        "3) 最后用一句话鼓励。\n"
        "禁止编造或改写任何数字与百分比；禁止医疗诊断；语气克制不说教。\n"
        f"事实：综合分 {payload.get('score')}，等级 {payload.get('grade')}，"
        f"连续达标 {payload.get('streak')} 周。\n"
        f"维度：{[(d['label'], d['score'], d.get('note') or '') for d in payload.get('dims') or []]}\n"
        f"本周任务：{[t.get('title') for t in payload.get('tasks') or []]}\n"
        "直接输出正文，不要标题。"
    )


def _coach_letter_bg(payload: dict, week: str, sig: str) -> None:
    """后台写 AI 周报并缓存；任何失败保留规则模板（不在请求线程里调 LLM）。"""
    global _coach_letter_busy
    try:
        out = _coach_letter_llm(_coach_letter_prompt(payload))
        import db
        if out and not out.startswith("⚠️"):
            db.kv_set(COACH_CACHE_KEY, json.dumps(
                {"week": week, "sig": sig, "text": out.strip(), "mode": "agent"},
                ensure_ascii=False))
    except Exception:
        pass
    finally:
        with _coach_letter_lock:
            _coach_letter_busy = False


def coach_letter(payload: dict, force: bool = False) -> dict:
    """写教练周报：同步只读缓存/回落模板，LLM 后台异步（GET 永不阻塞）。force=手动刷新时若缓存命中可直接返回。"""
    global _coach_letter_busy
    import db
    week = payload.get("week") or ""
    sig = coach_sig(payload)
    if os.environ.get("COACH_LLM", "1") == "0":
        return {"text": coach_letter_fallback(payload), "mode": "rules"}
    cached = db.kv_get(COACH_CACHE_KEY)
    if cached and not force:
        try:
            d = json.loads(cached)
            if d.get("week") == week and d.get("sig") == sig:
                return {"text": d["text"], "mode": d.get("mode", "agent")}
        except Exception:
            pass
    if cached and force:
        try:
            d = json.loads(cached)
            if d.get("week") == week and d.get("sig") == sig and d.get("mode") == "agent":
                return {"text": d["text"], "mode": "agent"}
        except Exception:
            pass
    text, mode = coach_letter_fallback(payload), "rules"
    if provider() and not _agent_failed:
        with _coach_letter_lock:
            if not _coach_letter_busy:
                _coach_letter_busy = True
                threading.Thread(
                    target=_coach_letter_bg, args=(payload, week, sig), daemon=True
                ).start()
    db.kv_set(COACH_CACHE_KEY, json.dumps(
        {"week": week, "sig": sig, "text": text, "mode": mode},
        ensure_ascii=False))
    return {"text": text, "mode": mode}


def coach_sig(payload: dict) -> str:
    """周报缓存签名：分数+各维分+未完成任务标题。"""
    parts = [str(payload.get("score")), payload.get("grade") or ""]
    for d in payload.get("dims") or []:
        parts.append(f"{d.get('key')}:{d.get('score')}")
    for t in payload.get("tasks") or []:
        if t.get("status") == "open":
            parts.append(f"t:{t.get('title')}")
    return "|".join(parts)


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
    """当前实际可用的供应商（熔断未到半开窗口的不算）。"""
    live = [p for p in _llm_candidates() if not _provider_skippable(p)]
    return live[0] if live else None


def status() -> dict:
    """供前端探测当前运行模式。"""
    prov = current_provider()
    return {"mode": "agent" if prov else "example",
            "has_key": bool(_llm_candidates()),
            "provider": prov}
