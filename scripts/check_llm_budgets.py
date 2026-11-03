#!/usr/bin/env python3
"""静态检查：所有 LLM 调用点必须**显式**配 max_tokens。

★ 为什么要有：默认 `llm_max_tokens: 8000` 对 reasoning 模型（MiMo）来说
  **思考 token 计入 max_tokens** —— 给紧了就 finish_reason='length' → content 空
  → 看起来像"没话说"（实测：事实核查整篇一次送 = 空返回）。
  新增调用点时忘了配预算 = 埋雷。

用法：python3 scripts/check_llm_budgets.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CORE = os.path.join(ROOT, "webapp", "core")

# 允许不显式配预算的调用点（短输出 / 内部重试）
ALLOW = {
    ("pipeline_ops.py", "call_llm(cfg, system, user, log=log)"),   # 内部兜底重试
}


def main():
    fails = []
    print("LLM 调用点的 max_tokens 预算：")
    for fn in sorted(os.listdir(CORE)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(CORE, fn)
        lines = open(path, encoding="utf-8").read().split("\n")
        for i, ln in enumerate(lines):
            # ★ 要覆盖 call_llm_stream —— 实测漏网之鱼恰恰在流式调用：
            #   它输出整篇讲课稿，却用的是默认 8000（下一个会炸的地方）。
            if ("call_llm(" not in ln and "call_llm_stream(" not in ln) \
                    or ln.strip().startswith("def "):
                continue
            # 取这条语句（含续行）看有没有 max_tokens
            stmt = ln
            j = i
            while "max_tokens" not in stmt and j + 1 < len(lines) and j - i < 4:
                j += 1
                stmt += " " + lines[j].strip()
            has = "max_tokens" in stmt
            key = (fn, ln.strip())
            if has:
                m = re.search(r"max_tokens=cfg\.get\(\"(\w+)\",\s*(\d+)\)", stmt)
                if m:
                    print(f"  ✓ {fn}:{i+1}  {m.group(1)} = {m.group(2)}")
                else:
                    print(f"  ✓ {fn}:{i+1}  （显式传参）")
            elif key in ALLOW:
                print(f"  ○ {fn}:{i+1}  兜底重试，允许不配")
            else:
                print(f"  ✗ {fn}:{i+1}  **没配 max_tokens** → 会落回默认 8000")
                fails.append(f"{fn}:{i+1}")
    print()
    if fails:
        print(f"FAIL: {len(fails)} 个调用点没配预算：{fails}")
        print("  → 每个都补上 max_tokens=cfg.get(\"<名字>\", <值>)")
        return 1
    print("PASS: 所有调用点都显式配了 token 预算")
    return 0


if __name__ == "__main__":
    sys.exit(main())
