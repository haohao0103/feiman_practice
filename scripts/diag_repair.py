#!/usr/bin/env python3
"""诊断：外科式修补为什么没触发（bench 那轮闸门报了"行动指令仅 2 处"却没修）。

用法：python3 scripts/diag_repair.py <课件md>
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod            # noqa: E402
from webapp.core import courseware_gen as cg        # noqa: E402
from webapp.core import pipeline_ops as ops         # noqa: E402


def log(m):
    print(f"  {time.strftime('%H:%M:%S')} {m}", flush=True)


def main():
    md_path = sys.argv[1]
    cfg = cfgmod.load()
    text = open(md_path, encoding="utf-8").read()
    script = text.split("\n---\n\n## 附一")[0]     # 只取正文部分

    checks, gate = cg.quality_gate(cfg, script, truncated=False)
    print("闸门判定：")
    for k in ("action_instructions", "check_questions", "body_cn_chars",
              "unexplained_terms"):
        print(f"   {k} = {checks.get(k)}")
    for g in gate:
        print(f"   ✗ {g}")
    if not gate:
        print("   （闸门通过，无需修补）")
        return 0

    print("\n调外科式修补（与 build_courseware 同一链）：")
    t0 = time.time()
    # A: 术语未当场解释 → 首次出现处插一句大白话
    if checks.get("unexplained_terms", 0):
        s1, r1 = cg.repair_unexplained_terms(cfg, script, checks, log=log)
        if s1 != script:
            c1, g1 = cg.quality_gate(cfg, s1, truncated=False)
            script, checks, gate = s1, c1, g1
            print(f"  → 术语修补后闸门：{g1 or '通过'}")
    # B: 行动指令/检验提问不足 → 只补缺的那一节
    if gate:
        s2, r2 = cg.repair_missing(cfg, script, checks, log=log)
        if s2 != script:
            c2, g2 = cg.quality_gate(cfg, s2, truncated=False)
            script, checks, gate = s2, c2, g2
            print(f"  → 清单修补后闸门：{g2 or '通过'}")
    print(f"  耗时 {time.time()-t0:.1f}s | 最终字数 {len(script)} | "
          f"闸门：{'通过 ✓' if not gate else gate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
