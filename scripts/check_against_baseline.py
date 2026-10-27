#!/usr/bin/env python3
"""录屏 vs 讲课稿 —— 逐项对账。

这是费曼学习法输出阶段的闭环工具：
  稿子（承诺值）  ×  录屏（实测值）  →  差距表  →  改稿

用法：
  # 1) 先生成/刷新稿子基线
  python3 scripts/check_script_quality.py
  # 2) 录完视频后跑评估流水线
  python3 scripts/pipeline.py --parallel --benchmark
  # 3) 对账
  python3 scripts/check_against_baseline.py

输出：outputs/录屏对账报告.md
      work/baseline_gap.json
日志：logs/baseline_gap.log
"""
import json
import os
import re
import sys
from datetime import datetime

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


WORK = os.path.join(ROOT, "work")
OUT = os.path.join(ROOT, "outputs", "录屏对账报告.md")
LOG = os.path.join(ROOT, "logs", "baseline_gap.log")
BASE = os.path.join(WORK, "script_baseline.json")
GAP = os.path.join(WORK, "baseline_gap.json")


def log(m):
    line = f"[gap] {m}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def J(p, default=None):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default if default is not None else {}


def main():
    base = J(BASE)
    if not base:
        log("FATAL: 缺 work/script_baseline.json，先跑 check_script_quality.py")
        return 2

    # 录屏实测
    meta = J(os.path.join(WORK, "probe_meta.json"))
    metrics = J(os.path.join(WORK, "metrics.json"))
    feyn = J(os.path.join(WORK, "feynman_scores.json"))
    vol = J(os.path.join(WORK, "volume_check.json"))
    vis = J(os.path.join(WORK, "visual_stats.json"))

    if not meta or not metrics:
        log("FATAL: 缺录屏实测数据（work/probe_meta.json / work/metrics.json）")
        log("      先跑：python3 scripts/pipeline.py --parallel --benchmark")
        return 2

    actual = {
        "duration_sec": meta.get("duration_sec", 0),
        "action_instructions": feyn.get("F4_教学闭环", {}).get("行动指令次数", 0),
        "check_questions": feyn.get("F4_教学闭环", {}).get("检验性提问次数", 0),
        "analogy_marks": feyn.get("F2_外行语言", {}).get("类比总数", 0),
        "term_per_1000cn": feyn.get("F2_外行语言", {}).get("术语每千字", 0),
        "repeat_ratio": vol.get("重复体量", {}).get("占总讲述比例", 0),
        "diagram_visible_ratio": vis.get("diagram_ratio", 0),
        "cn_chars": metrics.get("cn_chars", 0),
        "cpm": metrics.get("cpm_wall", 0),
    }

    # 承诺值
    promised = {
        "duration_sec": base.get("body_duration_sec", 0),
        "action_instructions": 3,
        "check_questions": 3,
        "analogy_marks": 8,
        "term_per_1000cn": None,     # 参考项，不设硬阈值
        "repeat_ratio": 0.10,
        "diagram_visible_ratio": 1.0,
    }

    def judge(key, a, p):
        """返回 (状态, 说明)"""
        if p is None:
            return "参考", f"{a}"
        if key == "duration_sec":
            lo, hi = 480, 600
            return ("达标" if lo <= a <= hi else "偏离",
                    f"{a/60:.1f} 分钟（目标 8~10）")
        if key == "repeat_ratio":
            return ("达标" if a <= p else "超标", f"{a*100:.1f}%（目标 ≤10%）")
        if key == "diagram_visible_ratio":
            return ("达标" if a >= p - 0.05 else "不足", f"{a*100:.0f}%（目标 100%）")
        return ("达标" if a >= p else "不足", f"{a}（目标 ≥{p}）")

    rows = []
    for key in ["duration_sec", "action_instructions", "check_questions",
                "analogy_marks", "term_per_1000cn", "repeat_ratio",
                "diagram_visible_ratio"]:
        a = actual.get(key)
        p = promised.get(key)
        if a is None:
            continue
        st, detail = judge(key, a, p)
        rows.append({"metric": key, "promised": p, "actual": a,
                     "status": st, "detail": detail,
                     "gap": (round(a - p, 2) if isinstance(a, (int, float))
                             and isinstance(p, (int, float)) else None)})

    n_bad = sum(1 for r in rows if r["status"] in ("偏离", "超标", "不足"))

    # 输出
    out = {"generated": datetime.now().isoformat(),
           "script": base.get("script"),
           "promised": promised, "actual": actual,
           "rows": rows, "n_fail": n_bad}
    with open(GAP, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    L = []
    A = L.append
    A("# 录屏对账报告 · 价格行为学 · 反转的五个步骤")
    A("")
    A(f"> 稿子：`{os.path.basename(base.get('script',''))}`")
    A(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M}")
    A(f"> 结论：**{'全部达标' if n_bad == 0 else f'{n_bad} 项不达标，需回炉改稿'}**")
    A("")
    A("## 逐项对账")
    A("")
    A("| 指标 | 稿子承诺 | 录屏实测 | 差 | 状态 |")
    A("|---|---|---|---|---|")
    NAME = {"duration_sec": "时长", "action_instructions": "行动指令",
            "check_questions": "检验性提问", "analogy_marks": "类比",
            "term_per_1000cn": "术语密度/千字", "repeat_ratio": "重复内容占比",
            "diagram_visible_ratio": "流程图可见率"}
    for r in rows:
        pr = "—" if r["promised"] is None else (
            f"{r['promised']/60:.1f}min" if r["metric"] == "duration_sec"
            else (f"{r['promised']*100:.0f}%" if r["metric"] in
                  ("repeat_ratio", "diagram_visible_ratio") else r["promised"]))
        ac = (f"{r['actual']/60:.1f}min" if r["metric"] == "duration_sec"
              else (f"{r['actual']*100:.0f}%" if r["metric"] in
                    ("repeat_ratio", "diagram_visible_ratio") else r["actual"]))
        gap = "—" if r["gap"] is None else f"{r['gap']:+.2f}"
        A(f"| {NAME.get(r['metric'], r['metric'])} | {pr} | {ac} | {gap} | "
          f"{'✅' if r['status'] in ('达标','参考') else '❌'} {r['detail']} |")
    A("")
    A("## 附加实测")
    A("")
    A(f"- 讲述字数：{actual['cn_chars']} 字")
    A(f"- 语速：{actual['cpm']} 字/分")
    A("")
    if n_bad:
        A("## 回炉清单（照这个改稿，下期必查）")
        A("")
        for r in rows:
            if r["status"] in ("偏离", "超标", "不足"):
                A(f"- [ ] **{NAME.get(r['metric'], r['metric'])}**："
                  f"实测 {r['detail']} → 改稿目标 {r['promised']}")
        A("")
    A("---")
    A("")
    A("*本报告由 `scripts/check_against_baseline.py` 自动生成，"
      "数字全部来自 `work/*.json` 实测。*")
    A("")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("\n".join(L))
    log(f"n_fail={n_bad}  ->  {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
