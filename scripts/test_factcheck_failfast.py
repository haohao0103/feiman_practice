#!/usr/bin/env python3
"""事实核查**失败必须显式报错**（不许静默当成"没查出错误"）。

★ 踩过：改成"分批 + 某批失败就跳过"之后，全部批次失败时会返回 [] →
  上层以为"没查出错误" → 静默通过。必须一批都不成功就抛错。

用法：python3 scripts/test_factcheck_failfast.py
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod            # noqa: E402
from webapp.core import courseware_gen as cg        # noqa: E402


def main():
    cfg = cfgmod.load()
    script = ("# 标题\n\n## 第一段\n\n内容一。\n\n"
              "## 第二段\n\n内容二。\n\n## 第三段\n\n内容三。\n")
    fails = []

    def check(name, cond, extra=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f"  {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    # ① 全部批次不可用 → 必须抛错
    orig = cg.ops.call_llm
    cg.ops.call_llm = lambda *a, **k: None
    try:
        cg.fact_check(cfg, "转写内容", script)
        check("全部批次失败 → 抛异常", False, "→ 竟然返回了")
    except RuntimeError as e:
        check("全部批次失败 → 抛异常", True, f"→ {str(e)[:44]}")
    finally:
        cg.ops.call_llm = orig

    # ② 有成功批次 → 正常返回
    calls = {"n": 0}
    def fake(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return None                      # 第一批失败
        return '[{"original":"内容一","issue":"错别字","correction":"内容一。","basis":"x"}]'
    cg.ops.call_llm = fake
    try:
        corr = cg.fact_check(cfg, "转写内容", script, batch_size=1)
        check("部分批次失败 → 仍返回结果并标注", bool(corr) and any(
            c.get("issue") == "__partial__" for c in corr), f"→ {len(corr)} 条")
        check("真实纠错被保留", any(c.get("issue") == "错别字" for c in corr))
    finally:
        cg.ops.call_llm = orig

    print()
    print("PASS: 失败语义正确" if not fails else f"FAIL: {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
