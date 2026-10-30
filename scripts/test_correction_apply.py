#!/usr/bin/env python3
"""事实准确性「改正闭环」测试：用真实课件 + 真实转写跑一遍 fact_check → 外科式改正。

验证：
  1) fact_check 能产出纠错
  2) apply_corrections 能把原句替换成正确说法
  3) 改正后原句不再出现在正文里
  4) 改正不破坏其余正文（字数变化在合理范围）

用法：python3 scripts/test_correction_apply.py [课件md] [转写txt]
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod            # noqa: E402
from webapp.core import courseware_gen as cg        # noqa: E402

DEFAULT_CW = os.path.join(ROOT, "outputs", "课件_bench_BV1rXXfBiECa", "讲课稿.md")
DEFAULT_ASR = os.path.join(ROOT, "work", "bench_task", "asr.txt")


def log(m):
    print(f"  {time.strftime('%H:%M:%S')} {m}", flush=True)


def _core_of(fix):
    """从纠正文本里取一段有辨识度的核心（去标点、去掉"应表述为/应为/应该"这类前缀）。"""
    import re
    s = re.sub(r"^(应表述为|应为|应改为|应该|建议|改成)\s*[“\"']?", "", (fix or "").strip())
    s = re.sub(r"[“\"']", "", s)
    s = re.sub(r"[，。；：、,.;:（）()【】\[\]…—\-\s]", "", s)
    # 取最长的一段连续中文（≥6 字）作为核心
    segs = re.findall(r"[一-鿿]{6,}", s)
    return max(segs, key=len) if segs else s[:12]


def main():
    cw = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CW
    asr = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ASR
    cfg = cfgmod.load()
    md = open(cw, encoding="utf-8").read()
    transcript = open(asr, encoding="utf-8").read()
    # 只取正文部分
    script = md.split("\n---\n\n## 附一")[0]
    print(f"课件 {os.path.basename(os.path.dirname(cw))}（正文 {len(script)} 字）")
    print(f"转写 {len(transcript)} 字")

    print("\n① fact_check 产出纠错")
    corrections = cg.fact_check(cfg, transcript, script, log=log)
    print(f"  纠错 {len(corrections)} 条")
    for c in corrections[:5]:
        print(f"    · [{c.get('issue')}] 原：{str(c.get('original'))[:40]}")
        print(f"       改：{str(c.get('correction'))[:50]}")

    if not corrections:
        print("  （本次没查出纠错 —— 测不了改正闭环）")
        return 0

    print("\n② 外科式改正")
    new_script, unresolved = cg.apply_corrections(script, corrections, log=log)
    print(f"  改正后 {len(script)} → {len(new_script)} 字；未找到原句 {len(unresolved)} 处")

    print("\n③ 验证：改正是否真的落到正文里")
    fixed = 0
    for c in corrections:
        orig = (c.get("original") or "").strip()
        fix = (c.get("correction") or "").strip()
        # ★ 正确验证方式：检查**纠正文本的核心意思**是否进了正文，
        #   而不是检查 original 是不是不在正文里（后者对"本来就不在"的串恒真 → 假通过）
        core = _core_of(fix)
        if core and core in new_script:
            fixed += 1
            print(f"    ✓ 已改进正文：{core[:26]}")
        else:
            still = orig[:20] in new_script
            print(f"    ✗ 未改进（原文{'仍在' if still else '已不在'}正文）："
                  f"{(core or fix)[:26]}")
    print(f"  改正落地 {fixed}/{len(corrections)} 处")

    print("\n④ 验证：其余正文没被破坏")
    print(f"  字数变化 {len(new_script) - len(script):+d}")
    print(f"  段数 {script.count(chr(10) + '## ')} → {new_script.count(chr(10) + '## ')}")

    ok = (not unresolved) and fixed >= 1
    print("\nPASS: 改正闭环成立" if ok else "\n注意：有未改正项，需人工看")
    return 0


if __name__ == "__main__":
    sys.exit(main())
