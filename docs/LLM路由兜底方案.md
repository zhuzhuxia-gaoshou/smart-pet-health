# LLM 路由兜底分类 — 最小落地方案（加餐 5 / Commit E）

目标：`_route` 落到 `default`（规则未命中）时，用一次轻量 LLM 分类归入四专家之一；任何失败回落 `general_agent`，行为与今天完全一致。规则命中永远优先，LLM 分支只在 `default` 触发。

## 1. 拓扑图

```
message
  │
  ├─ _route 规则层（0ms，确定性，优先级不变）
  │    draft > species-boundary > report > care > health(含弱信号+宠物名)
  │    │ 命中 → 直接返回 (expert, "rule:*")           ← 26 条 strict 用例，零变化
  │    ▼ 未命中
  ├─ [新增] LLM 分类节点（仅 default 分支，≤3s，单轮，A/B/C/D 单字母输出）
  │    │ 成功 → (expert, "llm:<label>")   label ∈ 四类
  │    │ 失败/超时/非法/熔断/无Key → 走原路径
  │    ▼
  ├─ general_agent ("default")                        ← 与今天逐字节一致
  ▼
专家 Agent（供应商链：百炼→DeepSeek→通义）→ 失败降级通用 → 规则模式
```

分类节点只改"选谁"，不改工具集、不改写路径：`create_record_draft` 仍只在 care_advisor 与 general_agent 手里，且 general_agent 兜底路线原样保留。

## 2. 失败路径枚举

| 场景 | 处理 |
|---|---|
| 分类超时（>3s） | 异常捕获 → `None` → 落 `general_agent/default`，连续失败计数 +1 |
| 返回非法标签（非 A-D） | 正则白名单校验失败视为失败，同上（计数 +1，不缓存） |
| 供应商全熔断/无 Key | `current_provider()` 为空或 `_agent_failed` → 直接跳过分类，0 等待 |
| 连续失败 ≥3 次 | 进程内置停用标记，后续 default 直接回落，不再白等 3s（与 `_dead_providers` 同思路） |
| `--routing-only` | 双保险：eval 置 `agent._LLM_ROUTE_OFF=True` + 默认 `LLM_ROUTE=0`，保证零 LLM 调用，26/26 不变 |
| 并发 | 分类无共享写状态；计数 `int += 1` GIL 下安全；最坏并发双发一次调用，单用户可接受 |
| 欠费(402) | 复用现有模式：熔断该供应商后顺延下一家，全部欠费时计数熔断关闭 LLM 路由 |

失败路径的原则：**LLM 分支的任何失败都不能产生与今天不同的用户可见行为**，只在 `route` 字段留下归因差异。

## 3. 最小 diff 草案（agent.py）

**插入位置 A**：`_ROUTE_HEALTH_WEAK` 定义之后、`_route` 之前（约 L152）。

```python
# ---- LLM 路由兜底分类（仅 default 分支触发；LLM_ROUTE=1 才启用）----
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
「给仓鼠买什么垫料好？」→ B
「写一首关于我家狗狗的小诗」→ C
「谢谢啦」→ D
用户消息：{message}
字母："""


def _llm_route_enabled() -> bool:
    return (not _LLM_ROUTE_OFF) and _LLM_ROUTE_FAILS < _LLM_ROUTE_FAIL_MAX \
        and os.environ.get("LLM_ROUTE", "0") == "1"


def _classify_llm(prov: str):
    """分类专用轻量 LLM：temperature=0、max_tokens=64（给思考块留余量）、超时 3s、不重试。"""
    if prov == "bailian":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=os.environ.get("BAILIAN_MODEL", "qwen3.8-flash"),
                             api_key=bailian_key(),
                             base_url=os.environ.get("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/apps/anthropic"),
                             temperature=0, max_tokens=64, timeout=3, max_retries=0)
    if prov == "deepseek":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
                          api_key=deepseek_key(),
                          base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                          temperature=0, max_tokens=64, timeout=3, max_retries=0)
    from langchain_community.chat_models.tongyi import ChatTongyi
    return ChatTongyi(model=os.environ.get("QWEN_MODEL", "qwen-plus"),
                      dashscope_api_key=dashscope_key(),
                      temperature=0, max_tokens=64, request_timeout=3)


def _cache_put(msg: str, label: str) -> None:
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
        if prov in _dead_providers:
            continue
        try:
            out = _classify_llm(prov).invoke([("user", ROUTE_CLASSIFY_PROMPT.format(message=msg))])
            content = out.content
            if isinstance(content, list):
                content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
            m = re.search(r"[ABCD]", (content or "").strip().upper())
            if not m:
                raise ValueError(f"非法分类输出: {(content or '')[:20]!r}")
            label = mapping[m.group(0)]
            _LLM_ROUTE_FAILS = 0
            _cache_put(msg, label)
            return label
        except Exception as e:
            txt = str(e)
            if "402" in txt or "Insufficient Balance" in txt or "Arrearage" in txt:
                _dead_providers.add(prov)
            continue
    _LLM_ROUTE_FAILS += 1
    if _LLM_ROUTE_FAILS >= _LLM_ROUTE_FAIL_MAX:
        print(f"[agent] LLM 路由分类连续失败 {_LLM_ROUTE_FAILS} 次，本进程内停用")
    return None
```

**插入位置 B**：`_route` 尾部，替换 `return "general_agent", "default"` 一行为：

```python
    if _llm_route_enabled():
        label = _llm_classify(msg)
        if label:
            return ("general_agent" if label == "general" else label), f"llm:{label}"
    return "general_agent", "default"
```

说明：成功且判为闲聊 → `llm:general`（与失败的 `default` 可区分，归因清晰）；失败路径与今天逐字节一致。main.py 的草稿重试文本命中 `rule:draft`，永不进 LLM 分支。前端 `expert_label` 逻辑零改动。

## 4. 评估用例与 eval_agent.py 改动

`eval_cases.json` 追加 5 条（`route_strict:false` + 新字段 `expect_llm_route:true`，已人工核对全部落 default）：

```json
{ "id": "L01", "message": "我该给可乐买什么牌子的狗粮？", "expected_expert": "care_advisor",
  "route_strict": false, "expect_llm_route": true, "must_contain": [], "any_of": [],
  "expect_draft": false, "allow_degraded": true, "note": "选购建议→护理顾问；现规则落 default" },
{ "id": "L02", "message": "可乐最近老是挠耳朵，是不是有耳螨？", "expected_expert": "health_analyst",
  "route_strict": false, "expect_llm_route": true, "must_contain": [], "any_of": [],
  "expect_draft": false, "allow_degraded": true, "note": "症状观察→健康分析师" },
{ "id": "L03", "message": "帮我写一段可乐的领养介绍文案", "expected_expert": "report_writer",
  "route_strict": false, "expect_llm_route": true, "must_contain": [], "any_of": [],
  "expect_draft": false, "allow_degraded": true, "note": "成文请求→报告撰稿人；「帮我写」不撞 draft 词表" },
{ "id": "L04", "message": "可乐和布丁谁更乖？", "expected_expert": "general_agent",
  "route_strict": false, "expect_llm_route": true, "must_contain": [], "any_of": [],
  "expect_draft": false, "allow_degraded": true, "note": "主观闲聊负样本：带宠物名也不许误入健康" },
{ "id": "L05", "message": "布丁吐了一次，要紧吗？", "expected_expert": "health_analyst",
  "route_strict": false, "expect_llm_route": true, "must_contain": [], "any_of": [],
  "expect_draft": false, "allow_degraded": true, "note": "症状担忧→健康分析师" }
```

`eval_agent.py` 三处最小改动：
1. `run()` 循环开头：`if c.get("expect_llm_route") and routing_only: print(f"{c['id']:<4} skip  （LLM 兜底用例，--routing-only 不计）"); continue`；并在 `main()` 设 `agent._LLM_ROUTE_OFF = True`（routing-only 时），双保险保证零调用。
2. `run()` 路由判定：`if c.get("expect_llm_route"): route_ok = res.get("route","").startswith("llm:") and routed == c["expected_expert"]`（仅全量模式可达）；行字典加 `"expect_llm_route": bool(c.get("expect_llm_route"))`。
3. `summarize()` 加一行 `LLM 兜底路由：x/5`（独立计数，不入 strict 分母）；`main()` 全量模式开头 `os.environ.setdefault("LLM_ROUTE", "1")`（load_dotenv 不覆盖已设变量，外部可强制关）。

验证顺序：`--routing-only` 26/26 且日志无 LLM 调用 → 全量 `LLM_ROUTE=1` 看 L 系列 5/5、G 系列不回归 → 刷新 `eval_baseline.json`。

## 5. 风险与回滚

- 回滚 = 一个环境变量：`.env` 或进程环境 `LLM_ROUTE=0`（或删掉该行），重启后行为与改动前逐字节一致；代码级回滚只 revert `_route` 尾部 4 行即可。
- 缓存取舍：进程内 FIFO 128 条，只缓存成功结果（失败可重试，熔断兜底）；同文本重发零延迟；代价是改提示词后需重启才生效——单用户可接受。
- 主要风险：qwen3.8-flash 若默认输出思考块，`max_tokens=64` 内可能被截断 → 解析失败 → 静默回落 general，不产生错误答案；连续 3 次即自动停用，不会拖慢后续闲聊。
- 每条 default 消息多 1 次 flash 级调用（输入约 300 token、输出 1 字母），费用可忽略；延迟上限 +3s，仅在规则未命中的闲聊上发生。

## 6. 结论建议

**值得落地，但按本方案的默认关闭形态合入**：单机单用户、百炼单活供应商、规则路由已 26/26，default 流量以闲聊为主，LLM 兜底收益边际；默认 `LLM_ROUTE=0` 合入（评估与归因基建全就位），白天想试再置 1——零风险拿到能力，不为此多付每条闲聊 3s 的最坏延迟。
