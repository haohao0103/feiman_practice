#!/usr/bin/env python3
"""逐字稿 vs 原视频 覆盖度校验。

校验三件事：
  1. **覆盖度**：每个 ASR 段是否都进入了逐字稿（无遗漏段落）
  2. **保真度**：逐字稿正文 vs 原 ASR 的字符重合率（防止过度清洗丢信息）
  3. **画面绑定**：每段引用的画面，其原视频时间点是否落在该段区间内

输出：work/script_coverage.json
日志：logs/script_coverage.log
"""
import json
import os
import re

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ASR_SRC = os.environ.get("FEIMAN_ORIGIN_REPO", "")
MD = os.path.join(ROOT, "outputs", "价格行为学_反转的五个步骤_逐字稿.md")
IDX = os.path.join(ROOT, "work", "course_frames_clean_index.json")
OUT = os.path.join(ROOT, "work", "script_coverage.json")
LOG = os.path.join(ROOT, "logs", "script_coverage.log")

import importlib.util

spec = importlib.util.spec_from_file_location(
    "b", os.path.join(ROOT, "scripts", "build_script_from_video.py"))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)

CN = re.compile(r"[\u4e00-\u9fff]")


def log(m):
    line = f"[cover] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    asr = B.parse_asr(ASR_SRC)
    md = open(MD, encoding="utf-8").read()
    frames = {it["key"]: it for it in json.load(open(IDX, encoding="utf-8"))}

    # --- 1. 覆盖度：逐段核对
    missing = []
    covered_chars = 0
    total_chars = 0
    for st, en, tx in asr:
        c = B.clean_speech(tx)
        cc = CN.findall(c)
        total_chars += len(cc)
        # 该段前 8 个实义字是否出现在 md 中
        probe = "".join(cc[:8])
        if len(probe) >= 4 and probe in md:
            covered_chars += len(cc)
        else:
            missing.append({"start": st, "end": en,
                            "probe": probe, "raw": tx[:60]})

    # --- 2. 保真度：逐字稿正文 vs ASR 全集 的 2-gram Dice
    md_body = "\n".join(l for l in md.splitlines()
                        if not l.startswith(("|", "#", "!", ">", "*", "-", "`", "---")))
    a = CN.findall("".join(B.clean_speech(t) for _, _, t in asr))
    b = CN.findall(md_body)
    from collections import Counter
    ga = Counter("".join(a[i:i + 2]) for i in range(len(a) - 1))
    gb = Counter("".join(b[i:i + 2]) for i in range(len(b) - 1))
    inter = sum((ga & gb).values())
    dice = 2 * inter / (sum(ga.values()) + sum(gb.values())) if (ga or gb) else 0

    # --- 3. 画面绑定：每张图的时间点应落在引用它的段落内
    seg_bounds = {}
    cur = None
    for l in md.splitlines():
        m = re.match(r"## 段 (\d+) · ", l)
        if m:
            cur = m.group(1)
            seg_bounds[cur] = None
        m2 = re.match(r"\*原视频 (\d+):(\d+) – (\d+):(\d+)\*", l)
        if m2 and cur:
            seg_bounds[cur] = (int(m2.group(1)) * 60 + int(m2.group(2)),
                               int(m2.group(3)) * 60 + int(m2.group(4)))
    misaligned = []
    checked = 0
    cur = None
    for l in md.splitlines():
        m = re.match(r"## 段 (\d+) · ", l)
        if m:
            cur = m.group(1)
        m2 = re.match(r"!\[(F\d+_[^\]]+)\]", l)
        if m2 and cur:
            key = m2.group(1)
            if key in frames and cur in seg_bounds and seg_bounds[cur]:
                checked += 1
                s, e = seg_bounds[cur]
                t = frames[key]["time_sec"]
                if not (s <= t <= e + 3):
                    misaligned.append({"seg": cur, "key": key,
                                       "t": t, "seg_range": [s, e]})

    out = {
        "asr_segments": len(asr),
        "asr_chars_cleaned": total_chars,
        "covered_chars": covered_chars,
        "coverage_ratio": round(covered_chars / total_chars, 4) if total_chars else 0,
        "missing_segments": missing,
        "fidelity_dice_2gram": round(dice, 4),
        "md_body_chars": len(b),
        "image_bindings_checked": checked,
        "image_misaligned": misaligned,
        "images_total": len(frames),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    log(json.dumps({k: v for k, v in out.items() if k != "missing_segments"},
                    ensure_ascii=False, indent=2))
    if missing:
        log("--- 缺失段落 ---")
        for m in missing:
            log(f"  {m['start']:.1f}-{m['end']:.1f}s  probe={m['probe']!r}")
    log("=== coverage done")


if __name__ == "__main__":
    main()
