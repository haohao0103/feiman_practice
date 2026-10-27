#!/usr/bin/env python3
"""交叉核验：稿子体量 vs 实际讲述体量；脚本自身元数据自洽性检查。

输出：work/volume_check.json
日志：logs/volume_check.log
"""
import json
import os
import re

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


WORK = os.path.join(ROOT, "work")
LOG = os.path.join(ROOT, "logs", "volume_check.log")
CN = re.compile(r"[\u4e00-\u9fff]")


def log(m):
    line = f"[vol] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    segs = json.load(open(os.path.join(WORK, "asr_segments.json"), encoding="utf-8"))
    spoken = "".join(s["text"] for s in segs)
    spoken_cn = len(CN.findall(spoken))

    # 稿子第1期（工作稿保存的中文片段）
    ref = open(os.path.join(WORK, "script_ref.txt"), encoding="utf-8").read()
    ref_cn = len(CN.findall(ref))

    # 稿子声明的元数据
    declared = {
        "总字数": 6289,
        "期数": 2,
        "第1期分钟": 6,
        "第2期分钟": 7,
        "声明语速字每分": 240,
    }
    total_min = declared["第1期分钟"] + declared["第2期分钟"]
    implied_capacity = total_min * declared["声明语速字每分"]

    # 实测：录屏只讲了第1期（0-1203s，"第一期内容我们到这里就结束了"）
    actual_min = 1203.6 / 60

    # 三遍重复的区间（来自 repetition.json 的语义轮次）
    passes = {
        "P1_五步主讲_第1遍": [89, 269],
        "P2_五步重讲_第2遍": [534, 622],
        "P3_五步重讲_第3遍": [695, 890],
        "三归宿_第1遍": [177, 302],
        "三归宿_第2遍": [935, 985],
    }
    rep_sec = 0
    for k, (a, b) in passes.items():
        c = sum(len(CN.findall(s["text"])) for s in segs if a <= s["start"] < b)
        log(f"  {k}: {a}-{b}s  中文{c}字")
    # 重复轮次（P2+P3 + 三归宿第2遍）的字量
    rep_chars = 0
    for k, (a, b) in passes.items():
        if k.startswith(("P2", "P3")) or k == "三归宿_第2遍":
            rep_chars += sum(len(CN.findall(s["text"])) for s in segs if a <= s["start"] < b)

    out = {
        "稿子第1期_中文字数": ref_cn,
        "录屏第1期_实际中文字数": spoken_cn,
        "倍差": round(spoken_cn / ref_cn, 2),
        "稿子元数据自洽性": {
            "声明": declared,
            "按声明语速_13分钟可承载字数": implied_capacity,
            "但声明总字数": declared["总字数"],
            "缺口": declared["总字数"] - implied_capacity,
            "自洽": declared["总字数"] <= implied_capacity,
        },
        "时长对比": {
            "计划第1期分钟": declared["第1期分钟"],
            "实际第1期分钟": round(actual_min, 1),
            "超时倍数": round(actual_min / declared["第1期分钟"], 2),
        },
        "重复体量": {
            "重复轮次区间": passes,
            "重复部分中文字数": rep_chars,
            "占总讲述比例": round(rep_chars / spoken_cn, 3),
        },
        "结构支点定义检查": {
            "出现次数": spoken.count("结构支点"),
            "全部出现上下文": [spoken[max(0, m.start() - 45):m.start() + 45]
                            for m in re.finditer("结构支点", spoken)],
        },
    }
    with open(os.path.join(WORK, "volume_check.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    log(json.dumps(out, ensure_ascii=False, indent=2))
    log("=== volume done")


if __name__ == "__main__":
    main()
