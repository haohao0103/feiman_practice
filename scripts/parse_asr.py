#!/usr/bin/env python3
"""把 ASR 转写落盘为可分析文本，并切分时间轴。

输入：work/audio_16k.wav -> 转写结果（本脚本读取 stdin 或已存 txt）
输出：work/asr_raw.txt, work/asr_segments.json
日志：logs/asr_parse.log
"""
import json
import os
import re
import sys

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


WORK = os.path.join(ROOT, "work")
LOG = os.path.join(ROOT, "logs", "asr_parse.log")
RAW = os.path.join(WORK, "asr_raw.txt")
SEG = os.path.join(WORK, "asr_segments.json")


def log(m):
    line = f"[asr_parse] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def hms_to_sec(t):
    parts = t.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    m, s = parts
    return int(m) * 60 + float(s)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else RAW
    text = open(src, encoding="utf-8").read()
    pat = re.compile(r"\[(\d+:\d+\.\d+),(\d+:\d+\.\d+),\d+\]\s*(.*)")
    segs = []
    for m in pat.finditer(text):
        segs.append({
            "start": round(hms_to_sec(m.group(1)), 2),
            "end": round(hms_to_sec(m.group(2)), 2),
            "text": m.group(3).strip(),
        })
    log(f"segments={len(segs)}")
    if segs:
        log(f"first={segs[0]['start']} last_end={segs[-1]['end']}")
    with open(SEG, "w", encoding="utf-8") as fh:
        json.dump(segs, fh, ensure_ascii=False, indent=1)
    log(f"written {SEG}")


if __name__ == "__main__":
    main()
