#!/usr/bin/env python3
"""实测：练习迭代追踪闭环（多轮归档 → 掌握度趋势 → 跨轮薄弱段 → 下一步建议）。

做法：同一份课件，跑**两轮真实对账**（第 2 轮用更短的试讲转写，模拟"练得不全"的
一次练习），两轮都走真实的对齐引擎 → 归档 → 渲染趋势图与练习报告。
不造假数据：分数全部来自真实引擎判定。
日志: work/practice_loop.log
"""
import os
import sys
import time

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)
from webapp.core import config as cfgmod          # noqa: E402
from webapp.core import pipeline_ops as ops       # noqa: E402
from webapp.core import rehearsal_review as rr    # noqa: E402
from webapp.core import practice_log as pl        # noqa: E402

LOG = os.path.join(ROOT, "work/practice_loop.log")
cfg = cfgmod.load()
COURSEWARE = os.path.join(ROOT, "outputs/课件_bench_BV1rXXfBiECa/讲课稿.md")
ASR = os.path.join(ROOT, "work/bench_task_BV1rXXfBiECa/asr.txt")


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def run_round(md, sections, segs, frac, label):
    """跑一轮真实对账并归档。"""
    n = max(1, int(len(segs) * frac))
    use = segs[:n]
    transcript = ops.asr_to_text(use)
    log(f"--- {label}：转写 {len(use)}/{len(segs)} 段（{frac:.0%}）")
    verdicts = rr.align_rehearsal(cfg, sections, transcript, log=log)
    planned = sum((s["duration_min"] or 0) for s in sections)
    actual_min = (use[-1][1] - use[0][0]) / 60 if use else 0
    mastery = rr.compute_mastery(verdicts, sections, actual_sec=actual_min * 60,
                                 planned_sec=planned * 60 or None)
    log(f"  {label} 掌握度 {mastery['score']} 分 · 覆盖 {mastery['coverage']*100:.0f}% "
        f"· 忠实 {mastery['fidelity']*100:.0f}% · 提问 {mastery['check_q_rate']} "
        f"· 行动 {mastery['action_rate']}")
    key = pl.practice_key_from_courseware(md, "practice")
    detail = mastery.get("detail", [])
    pl.archive_round(cfg, key, {
        "practice_title": key,
        "courseware_path": COURSEWARE,
        "trial": f"{label}（转写 {len(use)} 段）",
        "score": mastery["score"],
        "dims": {k: mastery.get(k) for k in
                 ("coverage", "fidelity", "check_q_rate", "action_rate",
                  "picture_rate", "duration_fit")},
        "segments": [{"idx": d.get("idx"), "title": d.get("title"),
                      "part": d.get("part"), "covered": d.get("covered"),
                      "missed": len(d.get("missed") or []),
                      "wrong": len(d.get("wrong") or []),
                      "comment": d.get("comment", "")} for d in detail],
    })
    return key, mastery


def main():
    open(LOG, "w", encoding="utf-8").close()
    log("=== 练习迭代追踪闭环测试 ===")
    md = open(COURSEWARE, encoding="utf-8").read()
    segs = ops.parse_asr_file(ASR)
    sections = rr.parse_courseware_sections(md)
    log(f"课件 {len(sections)} 段 / 试讲转写 {len(segs)} 段")

    key, m1 = run_round(md, sections, segs, 1.0, "第1轮·完整试讲")
    key2, m2 = run_round(md, sections, segs, 0.55, "第2轮·只讲了一半")

    data = pl.load_practice(cfg, key)
    trend = pl.compute_trend(data.get("rounds", []))
    weak = pl.recurring_weak_segments(data.get("rounds", []))
    log("=== 趋势 ===")
    log(f"  分数序列 {trend['scores']}  每轮增减 {trend['deltas']}  趋势 {trend['trend']}")
    log(f"=== 跨轮薄弱段 top5 ===")
    for w in weak:
        log(f"  {w['weak_rounds']}/{w['total']}  {w['part']} / {w['title']}  ({'；'.join(w['reasons'][:3])})")
    log("=== 下一步建议 ===")
    for t in pl.next_advice(data, trend, weak):
        log("  - " + t)

    pr = pl.render_practice(cfg, key)
    log(f"练习报告: {pr['report_path']}")
    log(f"趋势图:   {pr['chart_path']}")
    log(f"练习项目列表: {pl.list_practices(cfg)}")
    log("=== PASS ===" if pr.get("report_path") and pr.get("chart_path") else "=== FAIL ===")


if __name__ == "__main__":
    main()
