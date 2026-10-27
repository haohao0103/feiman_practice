#!/usr/bin/env python3
"""实测：课件 ↔ 试讲 逐段对齐 + 掌握度 + 改进稿。

用法: python3 scripts/test_rehearsal_alignment.py [课件md] [试讲asr.txt]
默认：标杆课件 + 原视频 ASR（当试讲）
日志: work/rehearsal_align.log
"""
import os
import sys
import time

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)
from webapp.core import config as cfgmod            # noqa: E402
from webapp.core import pipeline_ops as ops         # noqa: E402
from webapp.core import rehearsal_review as rr      # noqa: E402

LOG = os.path.join(ROOT, "work/rehearsal_align.log")
cfg = cfgmod.load()

COURSEWARE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "outputs/课件_bench_BV1rXXfBiECa/讲课稿.md")
TRIAL_ASR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    ROOT, "work/bench_task_BV1rXXfBiECa/asr.txt")


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def main():
    open(LOG, "w", encoding="utf-8").close()
    log("=== 试讲对齐引擎测试 ===")
    md = open(COURSEWARE, encoding="utf-8").read()
    segs = ops.parse_asr_file(TRIAL_ASR)
    transcript = ops.asr_to_text(segs)
    actual_min = (segs[-1][1] - segs[0][0]) / 60 if segs else 0
    log(f"课件: {COURSEWARE}")
    log(f"试讲: {TRIAL_ASR}（{len(segs)} 段 / {actual_min:.1f} 分钟）")

    sections = rr.parse_courseware_sections(md)
    log(f"课件解析出 {len(sections)} 段")
    for s in sections[:3]:
        log(f"  段样例: [{s['part']}] {s['title']} | 提问{len(s['check_questions'])} "
            f"行动{len(s['actions'])} 画面{len(s['pictures'])} 时长{s['duration_min']}")

    verdicts = rr.align_rehearsal(cfg, sections, transcript, log=log)
    log(f"对齐结果 {len(verdicts.get('segments', []))} 段")

    planned = sum((s["duration_min"] or 0) for s in sections)
    mastery = rr.compute_mastery(verdicts, sections, actual_sec=actual_min * 60,
                                 planned_sec=planned * 60 or None)
    log("=== 掌握度 ===")
    log(f"总分 {mastery['score']} 分 · {rr.mastery_grade(mastery['score'])}")
    log(f"覆盖 {mastery['coverage']*100:.0f}% (全{mastery['segments_covered']}/"
        f"半{mastery['segments_partial']}/漏{mastery['segments_missed']})")
    log(f"忠实 {mastery['fidelity']*100:.0f}% · 说错 {mastery['wrong_count']} 处")
    log(f"提问执行 {mastery['check_q_rate']} · 行动执行 {mastery['action_rate']} · "
        f"画面引用 {mastery['picture_rate']} · 时长匹配 {mastery['duration_fit']}")
    log(f"summary: {mastery['summary']}")

    meta = {"title": "价格行为学·反转五步骤", "courseware": COURSEWARE,
            "trial": os.path.basename(TRIAL_ASR), "actual_min": actual_min,
            "time": time.strftime("%Y-%m-%d %H:%M")}
    out = os.path.join(ROOT, "outputs", "试讲对账报告.md")
    rep = rr.build_alignment_report(mastery, sections, meta)
    with open(out, "w", encoding="utf-8") as f:
        f.write(rep)
    log(f"对账报告: {out}")

    nxt = rr.build_next_draft(cfg, md, mastery, log=log)
    nxt_path = os.path.join(ROOT, "outputs", "改进稿_对账版.md")
    with open(nxt_path, "w", encoding="utf-8") as f:
        f.write(nxt)
    log(f"改进稿: {nxt_path}（{len(nxt)} 字）")


if __name__ == "__main__":
    main()
