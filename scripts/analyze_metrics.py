#!/usr/bin/env python3
"""费曼法评估量化：从 ASR 段计算语速、口头禅、重复、停顿、提问、术语密度，
并与逐字稿做相似度比对，判断"念稿"还是"讲自己的话"。

输出：work/metrics.json, logs/metrics.log
"""
import json
import os
import re
from collections import Counter

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


WORK = os.path.join(ROOT, "work")
LOG = os.path.join(ROOT, "logs", "metrics.log")
SEG = os.path.join(WORK, "asr_segments.json")
OUT = os.path.join(WORK, "metrics.json")
SCRIPT = os.path.join(WORK, "script_ref.txt")


def log(m):
    line = f"[metrics] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


CN = re.compile(r"[\u4e00-\u9fff]")
FILLERS = ["呃", "那个", "这个", "对吧", "是不是", "然后呢", "就是说",
           "其实", "我们说", "啊这", "对对对", "嗯", "OOK", "OK"]
# 价格行为学核心术语（用于密度）
TERMS = ["遇阻", "极值", "趋势线", "前高", "前低", "结构支点", "高点上移",
         "低点下移", "假突破", "交易区间", "通道", "之字", "回调", "反向K线",
         "深度回调", "三推", "双顶", "双底", "趋势"]


def cn_count(s):
    return len(CN.findall(s))


def main():
    segs = json.load(open(SEG, encoding="utf-8"))
    total_chars = sum(cn_count(s["text"]) for s in segs)
    speak_time = sum(s["end"] - s["start"] for s in segs)
    wall = segs[-1]["end"] - segs[0]["start"]

    # 停顿：相邻段间隔
    gaps = []
    for a, b in zip(segs, segs[1:]):
        g = b["start"] - a["end"]
        if g >= 0.8:
            gaps.append((round(a["end"], 1), round(g, 1), b["text"][:28]))
    gaps.sort(key=lambda x: -x[1])

    # 口头禅
    filler_cnt = {}
    full = "".join(s["text"] for s in segs)
    for f in FILLERS:
        filler_cnt[f] = full.count(f)

    # 术语密度
    term_cnt = {t: full.count(t) for t in TERMS if full.count(t) > 0}

    # 疑问句/互动
    q_marks = full.count("？") + full.count("?")
    follow = len(re.findall(r"能不能跟上|大家能明白|能明白吗|对不对|大家看|我们来看|注意|看这里", full))

    # 与逐字稿相似度（字符级 2-gram Dice）
    script_txt = ""
    if os.path.exists(SCRIPT):
        script_txt = open(SCRIPT, encoding="utf-8").read()
    sim = None
    if script_txt:
        a = CN.findall(script_txt)
        b = CN.findall(full)
        ga = Counter("".join(a[i:i + 2]) for i in range(len(a) - 1))
        gb = Counter("".join(b[i:i + 2]) for i in range(len(b) - 1))
        inter = sum((ga & gb).values())
        tot = sum(ga.values()) + sum(gb.values())
        sim = round(2 * inter / tot, 4) if tot else None

    # 段落级重复：把讲稿按句切，看讲了多少次近似段落
    sentences = [s for s in re.split(r"[。？?，,；;]", full) if cn_count(s) >= 4]
    sc = Counter(cn_count(s) and s.replace(" ", "") for s in sentences)
    dups = [(k, v) for k, v in sc.items() if v >= 2]

    m = {
        "wall_sec": round(wall, 1),
        "speak_sec": round(speak_time, 1),
        "speech_ratio": round(speak_time / wall, 3),
        "cn_chars": total_chars,
        "cpm_wall": round(total_chars / wall * 60, 1),
        "cpm_speak": round(total_chars / speak_time * 60, 1),
        "segments": len(segs),
        "pause_gte_0.8s": len(gaps),
        "pause_top10": gaps[:10],
        "filler_counts": filler_cnt,
        "filler_total": sum(filler_cnt.values()),
        "filler_per_1000cn": round(sum(filler_cnt.values()) / total_chars * 1000, 1),
        "term_counts": term_cnt,
        "term_total": sum(term_cnt.values()),
        "term_per_1000cn": round(sum(term_cnt.values()) / total_chars * 1000, 1),
        "q_marks": q_marks,
        "interactive_markers": follow,
        "script_dice_2gram": sim,
        "duplicate_sentences": dups[:15],
        "dup_sentence_count": len(dups),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=2)
    log(json.dumps(m, ensure_ascii=False, indent=2))
    log("=== metrics done")


if __name__ == "__main__":
    main()
