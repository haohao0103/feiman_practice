#!/usr/bin/env python3
"""验证**分批**事实核查：跑真实 payload（失败任务的转写 + 真实成稿）。

背景：整篇一次送 → 模型跑 170~210 秒后返回**空内容**（两通道都这样）
→ 事实核查整步失败 → 20 分钟的成稿流水线作废。
分批后每批只带几段 → 应该十几~几十秒返回。

用法：python3 scripts/test_factcheck_batched.py [转写txt] [成稿md]
"""
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod            # noqa: E402
from webapp.core import courseware_gen as cg        # noqa: E402

DEFAULT_ASR = os.path.join(ROOT, "work", "projects",
                           "video_to_script_20261103_002912_1810f9", "asr.txt")
DEFAULT_MD = os.path.join(ROOT, "outputs",
                          "课件_video_to_script_20261103_005319_c4c05e", "讲课稿.md")


def log(m):
    print(f"  {time.strftime('%H:%M:%S')} {m}", flush=True)


def main():
    asr = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ASR
    md = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MD
    cfg = cfgmod.load()
    transcript = open(asr, encoding="utf-8").read()
    text = open(md, encoding="utf-8").read()
    m = re.search(r"^##\s*附一", text, re.M)
    script = text[:m.start()] if m else text
    segs = cg._split_script_segments(script)
    print(f"转写 {len(transcript)} 字符 | 稿子正文 {len(script)} 字符 | "
          f"切出 {len(segs)} 段")
    print(f"单批 payload ≈ {len(transcript)} + {len(script)//max(1,len(segs))*5} 字符"
          f"（原来一次送 {len(transcript)+len(script)} 字符）\n")

    t0 = time.time()
    corr = cg.fact_check(cfg, transcript, script, log=log, batch_size=5)
    print(f"\n  耗时 {time.time()-t0:.0f}s | 纠错 {len(corr)} 条")
    for c in corr[:6]:
        print(f"   · [{c.get('issue')}] {str(c.get('original'))[:40]}")
        print(f"     → {str(c.get('correction'))[:56]}")
    ok = len(corr) > 0
    print("\nPASS: 分批事实核查有产出" if ok else "\nFAIL: 仍然没有产出")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
