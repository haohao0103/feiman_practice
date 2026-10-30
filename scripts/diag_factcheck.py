#!/usr/bin/env python3
"""诊断 fact_check 为什么返回 0 条 —— 是"真没错误"还是"JSON 解析失败被静默吞了"。

★ 静默通过比没有校验更危险：解析失败时 fact_check 返回 []，
  流水线照样出稿、闸门照样通过 —— 但纠错环节其实根本没跑成。

用法：python3 scripts/diag_factcheck.py <课件md> <转写txt>
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod            # noqa: E402
from webapp.core import courseware_gen as cg        # noqa: E402
from webapp.core import pipeline_ops as ops         # noqa: E402


def main():
    cw = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "outputs", "课件_bench_BV1rXXfBiECa", "讲课稿.md")
    asr = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        ROOT, "work", "bench_task", "asr.txt")
    cfg = cfgmod.load()
    md = open(cw, encoding="utf-8").read()
    transcript = open(asr, encoding="utf-8").read()
    script = md.split("\n---\n\n## 附一")[0]

    user = (f"【原始转写】\n{transcript}\n\n【生成的讲课稿】\n{script}\n\n"
            f"请输出纠错 JSON 列表。")
    t0 = time.time()
    raw = ops.call_llm(cfg, cg._FACT_SYS, user)
    print(f"LLM 原始输出 {len(raw or '')} 字符，用时 {time.time()-t0:.0f}s")
    if not raw:
        print("✗ LLM 返回空 —— 双通道都不可用（不是'没有错误'）")
        return 1
    print("\n--- 原始输出前 600 字 ---")
    print((raw or "")[:600])
    parsed = cg._extract_json(raw)
    print("\n--- _extract_json 解析结果 ---")
    print(f"  类型: {type(parsed).__name__}")
    if isinstance(parsed, list):
        print(f"  条数: {len(parsed)}")
        for c in parsed[:5]:
            print(f"   · [{c.get('issue')}] original={str(c.get('original'))[:40]!r}")
    elif isinstance(parsed, dict):
        print(f"  keys: {list(parsed.keys())}")
        if "corrections" in parsed:
            print(f"  条数: {len(parsed['corrections'])}")
    else:
        print("  ✗ 解析失败（既不是 list 也不是 dict）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
