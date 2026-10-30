#!/usr/bin/env python3
"""看外科式改正的**实际上下文** —— 机械替换会不会造出病句/读着别扭。

用法：python3 scripts/inspect_correction_context.py <课件md> <转写txt>
"""
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod            # noqa: E402
from webapp.core import courseware_gen as cg        # noqa: E402


def log(m):
    print(f"  {time.strftime('%H:%M:%S')} {m}", flush=True)


def main():
    cw = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "outputs", "课件_bench_BV1rXXfBiECa", "讲课稿.md")
    asr = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        ROOT, "work", "bench_task", "asr.txt")
    cfg = cfgmod.load()
    md = open(cw, encoding="utf-8").read()
    transcript = open(asr, encoding="utf-8").read()
    script = md.split("\n---\n\n## 附一")[0]

    corrections = cg.fact_check(cfg, transcript, script, log=log)
    print(f"\n纠错 {len(corrections)} 条\n")
    for i, c in enumerate(corrections, 1):
        orig = (c.get("original") or "").strip()
        fix = (c.get("correction") or "").strip()
        span = cg._find_span(script, orig)
        if not span:
            for cand in cg._shorten_candidates(orig):
                span = cg._find_span(script, cand)
                if span:
                    break
        print(f"── {i}. [{c.get('issue')}]")
        if not span:
            print(f"   原句: {orig[:60]}")
            print(f"   ⚠ 找不到原句")
            continue
        s, e = span
        before = script[max(0, s - 40):s]
        after = script[e:e + 40]
        print(f"   改前: …{before}【{orig}】{after}…")
        print(f"   改后: …{before}【{fix}】{after}…")
        print()


if __name__ == "__main__":
    main()
