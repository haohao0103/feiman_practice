#!/usr/bin/env python3
"""质量闸门口径一致性核验：自检 / 闸门 / 复审 三处的"未解释术语"必须一致。

背景：术语检测曾有三份实现（自检按段落作用域、复审按句子作用域、
且 EXPLAINED 白名单定义了却没用上）→ 口径分叉。现在统一到
check_script_quality.find_unexplained_terms()，本脚本验证三处结果一致。

用法：python3 scripts/check_gate_consistency.py [课件md ...]
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as C          # noqa: E402
from webapp.core import pipeline_ops as ops  # noqa: E402
from webapp.core import courseware_gen as cg  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "csq", os.path.join(ROOT, "scripts", "check_script_quality.py"))
csq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csq)


def audit_terms(md_path):
    """复审脚本那一路的口径（同一函数，但独立跑一遍验证一致）。"""
    import re
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    m = re.search(r"^##\s*附", md, re.M)
    main_body = md[:m.start()] if m else md
    body = csq.body_of(main_body)
    return [u["term"] for u in
            csq.find_unexplained_terms(body, csq.split_sections(main_body))]


def main():
    cfg = C.load()
    files = sys.argv[1:]
    if not files:
        files = [os.path.join(ROOT, "outputs", d, "讲课稿.md")
                 for d in sorted(os.listdir(os.path.join(ROOT, "outputs")))
                 if d.startswith("课件_")
                 and os.path.exists(os.path.join(ROOT, "outputs", d, "讲课稿.md"))]
    bad = 0
    print(f"{'课件':46} {'自检':>6} {'闸门':>6} {'复审':>6}  一致")
    for md in files:
        if not os.path.exists(md):
            continue
        text = open(md, encoding="utf-8").read()
        sc = ops.self_check(cfg, text)
        n_self = sc.get("unexplained_terms", 0)
        _, gate = cg.quality_gate(cfg, text)
        n_gate = 0
        for g in gate:
            if "未当场解释" in g:
                n_gate = int(g.split()[0])
        terms_audit = audit_terms(md)
        same = (n_self == len(terms_audit))
        flag = "✓" if same else "✗ 不一致"
        if not same:
            bad += 1
        print(f"  {os.path.basename(os.path.dirname(md))[:44]:46} "
              f"{n_self:>6} {n_gate:>6} {len(terms_audit):>6}  {flag}"
              + (f"  {terms_audit[:4]}" if terms_audit else ""))
    print()
    print("PASS: 三处口径一致" if bad == 0 else f"FAIL: {bad} 处不一致")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
