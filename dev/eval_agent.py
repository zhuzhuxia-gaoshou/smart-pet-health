# -*- coding: utf-8 -*-
"""eval_agent.py — 分层多专家 Agent 评估运行器（进程内直调 agent.answer，不起 FastAPI）。

用法（在仓库根目录）：
  backend/.venv/Scripts/python.exe dev/eval_agent.py                  # 全量（真实 LLM）
  backend/.venv/Scripts/python.exe dev/eval_agent.py --routing-only   # 只测路由规则，0 成本
  backend/.venv/Scripts/python.exe dev/eval_agent.py --only C03,C06   # 指定用例
  backend/.venv/Scripts/python.exe dev/eval_agent.py --report dev/eval_report.json

打分维度（每条独立）：
  路由分：answer 返回的 expert == expected_expert（仅 route_strict 用例计入准确率分母）
  事实分：文本归一化后 must_contain 全命中 且 any_of 至少命中一
  草稿分：expect_draft 时 tools.take_last_draft().type == expected_draft_type；example 模式记 skip
  降级分：mode == example 时须 allow_degraded，否则 fail；requires_llm 用例在 example 下记 skip
阈值：路由准确率 ≥ 80%、事实命中率 ≥ 85%、草稿用例 0 fail → exit 0，否则 exit 1。
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.chdir(os.path.join(ROOT, "backend"))

import db      # noqa: E402
import agent   # noqa: E402
import tools   # noqa: E402


def _norm(s: str) -> str:
    """全角→半角、去空白、小写，降低事实匹配的排版噪声。"""
    out = []
    for ch in s or "":
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            ch = chr(code - 0xFEE0)
        out.append(ch)
    return "".join(out).replace(" ", "").replace("\n", "").lower()


def run(cases: list[dict], routing_only: bool) -> list[dict]:
    rows = []
    for c in cases:
        t0 = time.time()
        if c.get("expect_llm_route") and routing_only:
            # LLM 兜底用例依赖真实分类调用，--routing-only 下不计（双保险：main() 已置 _LLM_ROUTE_OFF）
            print(f"{c['id']:<4} skip  （LLM 兜底用例，--routing-only 不计）")
            continue
        if routing_only:
            expert, src = agent._route(c["message"])
            res = {"expert": expert, "route": src, "mode": "routing-only", "reply": ""}
            draft = None
        else:
            res = agent.answer(c["message"], history=c.get("history"))
            draft = tools.take_last_draft()
        dt = int((time.time() - t0) * 1000)
        expert = res.get("expert") if res.get("mode") != "example" else None
        # 路由判定：example 模式下 answer 返回 expert=none，但路由本身仍可用 route 字段回推
        routed = agent._route(c["message"])[0] if res.get("mode") == "example" else expert
        if c.get("expect_llm_route"):
            # L 系列要求路由来源是 LLM 兜底（llm:*）且专家正确；规则命中视为未生效
            route_ok = str(res.get("route", "")).startswith("llm:") and routed == c["expected_expert"]
        else:
            route_ok = routed == c["expected_expert"]

        text = _norm(res.get("reply", ""))
        must_ok = all(_norm(k) in text for k in c.get("must_contain", []))
        any_ok = (not c.get("any_of")) or any(_norm(k) in text for k in c["any_of"])
        fact_ok = None if routing_only else (must_ok and any_ok)

        degraded = res.get("mode") == "example"
        if routing_only:
            draft_ok, degrade_ok = None, None
        else:
            if c.get("expect_draft"):
                draft_ok = "skip" if degraded else bool(draft and draft.get("type") == c.get("expected_draft_type"))
            else:
                draft_ok = None
            if degraded and c.get("requires_llm"):
                degrade_ok = "skip"
            else:
                degrade_ok = (not degraded) or bool(c.get("allow_degraded"))
        rows.append({
            "id": c["id"], "message": c["message"], "expected": c["expected_expert"],
            "expert": routed, "route": res.get("route"), "mode": res.get("mode"),
            "route_strict": bool(c.get("route_strict", True)),
            "expect_llm_route": bool(c.get("expect_llm_route")),
            "route_ok": route_ok, "fact_ok": fact_ok, "draft_ok": draft_ok, "degrade_ok": degrade_ok,
            "latency_ms": dt, "reply_head": (res.get("reply") or "")[:80].replace("\n", " "),
        })
        sym = lambda v: "·" if v is None else ("skip" if v == "skip" else ("ok" if v else "FAIL"))
        print(f"{c['id']:<4} {sym(route_ok):<5} fact={sym(fact_ok):<5} draft={sym(draft_ok):<5} "
              f"degr={sym(degrade_ok):<5} {routed or '-':<15} {res.get('route') or '':<22} {dt:>6}ms  {c['message']}")
    return rows


def summarize(rows: list[dict], routing_only: bool, thresholds: dict) -> int:
    strict = [r for r in rows if r["route_strict"]]
    route_acc = sum(r["route_ok"] for r in strict) / max(1, len(strict))
    print("\n" + "=" * 72)
    print(f"路由准确率（route_strict）：{sum(r['route_ok'] for r in strict)}/{len(strict)} = {route_acc:.0%}"
          f"（阈值 {thresholds.get('route_accuracy', .8):.0%}）")
    loose = [r for r in rows if not r["route_strict"] and not r.get("expect_llm_route")]
    if loose:
        print(f"闲聊类路由（不计分母）：{sum(r['route_ok'] for r in loose)}/{len(loose)}")
    llm_routed = [r for r in rows if r.get("expect_llm_route")]
    if llm_routed:
        print(f"LLM 兜底路由（不计分母）：{sum(r['route_ok'] for r in llm_routed)}/{len(llm_routed)}")
    failed = route_acc < thresholds.get("route_accuracy", .8)
    if not routing_only:
        facts = [r for r in rows if r["fact_ok"] is not None]
        fact_rate = sum(r["fact_ok"] for r in facts) / max(1, len(facts))
        drafts = [r for r in rows if r["draft_ok"] is not None]
        draft_fail = sum(1 for r in drafts if r["draft_ok"] is False)
        degr_fail = sum(1 for r in rows if r["degrade_ok"] is False)
        modes = {}
        for r in rows:
            modes[r["mode"]] = modes.get(r["mode"], 0) + 1
        print(f"事实命中率：{sum(r['fact_ok'] for r in facts)}/{len(facts)} = {fact_rate:.0%}"
              f"（阈值 {thresholds.get('fact_hit_rate', .85):.0%}）")
        print(f"草稿用例：{len(drafts)} 条，fail {draft_fail}，skip {sum(1 for r in drafts if r['draft_ok'] == 'skip')}")
        print(f"降级违规：{degr_fail} 条 ｜ 模式分布：{modes} ｜ 平均耗时 {sum(r['latency_ms'] for r in rows) // max(1, len(rows))}ms")
        failed = failed or fact_rate < thresholds.get("fact_hit_rate", .85) or draft_fail > 0 or degr_fail > 0
    print("结果：" + ("❌ 未达阈值" if failed else "✅ 通过"))
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--routing-only", action="store_true", help="只测路由规则，不调用 LLM")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    ap.add_argument("--report", default="", help="把逐条结果写入 JSON 文件")
    args = ap.parse_args()

    with open(os.path.join(ROOT, "dev", "eval_cases.json"), encoding="utf-8") as f:
        suite = json.load(f)
    cases = suite["cases"]
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        cases = [c for c in cases if c["id"] in wanted]

    db.init_and_seed()
    if args.routing_only:
        agent._LLM_ROUTE_OFF = True          # 双保险之一：routing-only 绝不发起 LLM 分类调用
    else:
        os.environ.setdefault("LLM_ROUTE", "1")   # 全量模式默认开 LLM 兜底；外部设 0 可强制关
    if not db.list_pets():
        print("数据库无宠物，事实断言无法成立；请先启动一次应用生成种子数据。")
        return 1
    print(f"用例 {len(cases)} 条 ｜ 模式：{'仅路由' if args.routing_only else '真实 LLM'} ｜ 供应商：{agent.current_provider() or '无（规则模式）'}\n")
    rows = run(cases, args.routing_only)
    code = summarize(rows, args.routing_only, suite.get("thresholds", {}))
    if args.report:
        with open(os.path.join(ROOT, args.report) if not os.path.isabs(args.report) else args.report,
                  "w", encoding="utf-8") as f:
            json.dump({"routing_only": args.routing_only, "rows": rows}, f, ensure_ascii=False, indent=2)
        print(f"报告已写入 {args.report}")
    return code


if __name__ == "__main__":
    sys.exit(main())
