#!/usr/bin/env python3
"""费曼学习法四维量化：
F1 概念边界  F2 外行语言(类比/术语)  F3 卡壳处理  F4 教别人能学会(检验机制)

同时核验"自制图 A 是否随讲解高亮第 N 步"（逐帧比对流程图区域像素变化）。
输出：work/feynman_scores.json
日志：logs/feynman.log
"""
import json
import os
import re

import numpy as np
from PIL import Image

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


WORK = os.path.join(ROOT, "work")
FRAMES = os.path.join(ROOT, "work", "frames")
LOG = os.path.join(ROOT, "logs", "feynman.log")
CN = re.compile(r"[\u4e00-\u9fff]")


def log(m):
    line = f"[feynman] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# 类比/具象化标记
ANALOGY = ["就像", "好比", "相当于", "好像", "打个比方", "你可以理解为",
           "比如说", "例如说", "想象", "就跟我", "这就跟", "像是"]
# 自造术语（无标准出处，需要自解释）
JARGON = ["结构支点", "之字", "极值点", "遇阻", "假突破"]
# 观众检验性提问
CHECK_Q = ["大家能不能", "能明白吗", "大家看", "听懂了吗", "有没有问题",
           "觉得对吗", "是这样吗", "对不对"]
# 教学行动指令（让观众做事）
ACTION = ["拿你自己", "复盘", "标一下", "去练", "试试", "做一遍", "作业",
          "对照着", "打开图", "截图", "下一期", "我们下期"]


def load_small(p, w=480):
    im = Image.open(p).convert("RGB").resize((w, int(w * 2240 / 3584)))
    return np.asarray(im).astype(np.int16)


def diagram_highlight():
    """自制流程图出现在 100-130s / 230-260s 区间。
    逐帧比较该区域像素，判定是否有'高亮某一步'的显式变化。"""
    out = []
    for idx in list(range(9, 14)) + list(range(22, 27)):
        f = f"f_{idx:04d}.jpg"
        p = os.path.join(FRAMES, f)
        if not os.path.exists(p):
            continue
        a = load_small(p)
        h, w, _ = a.shape
        # 下半屏（流程图区域）彩色像素的空间分布
        lower = a[int(h * 0.55):, :, :]
        mx = lower.max(axis=2)
        mn = lower.min(axis=2)
        sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
        mask = (sat > 0.45) & (mx > 100)
        ys, xs = np.nonzero(mask)
        bbox = None
        if len(xs):
            bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()),
                    int(len(xs))]
        out.append({"idx": idx, "sec": idx * 10, "colored_px": int(mask.sum()),
                    "bbox": bbox})
    # 相邻帧差异
    diffs = []
    for a, b in zip(out, out[1:]):
        if a["idx"] + 1 == b["idx"]:
            diffs.append({"between": [a["idx"], b["idx"]],
                          "colored_px_delta": b["colored_px"] - a["colored_px"],
                          "bbox_moved": a["bbox"] != b["bbox"] if a["bbox"] and b["bbox"] else None})
    return {"frames": out, "diffs": diffs}


def main():
    segs = json.load(open(os.path.join(WORK, "asr_segments.json"), encoding="utf-8"))
    full = "".join(s["text"] for s in segs)
    chars = len(CN.findall(full))

    counts = {t: full.count(t) for t in ANALOGY}
    ana_total = sum(counts.values())
    jarg = {t: full.count(t) for t in JARGON}
    check = {t: full.count(t) for t in CHECK_Q}
    act = {t: full.count(t) for t in ACTION}

    # 术语首次出现位置 vs 是否在同段/邻近段被解释
    first_def = {}
    for t in JARGON:
        idx = full.find(t)
        first_def[t] = {
            "first_char_pos": idx,
            "context": full[max(0, idx - 60):idx + 60] if idx >= 0 else "",
        }

    # 自我纠正/卡壳
    self_fix = []
    for s in segs:
        if re.search(r"讲错|好像讲错|不不|等等|不对|重来|搞混|错了", s["text"]):
            self_fix.append([s["start"], s["text"][:100]])

    # 悬空提问（提问后 15s 内无对应解答标记）
    dangling = []
    for i, s in enumerate(segs):
        if any(k in s["text"] for k in CHECK_Q):
            nxt = "".join(x["text"] for x in segs[i + 1:i + 4])
            if not any(k in nxt for k in ["因为", "所以", "也就是说", "我们看", "好，", "对"]):
                dangling.append([s["start"], s["text"][:60]])

    # 结论回扣：讲完全部内容后是否给出"接下来做什么"
    tail = "".join(s["text"] for s in segs[-12:])
    action_in_tail = [k for k in ACTION if k in tail]

    scores = {
        "cn_chars": chars,
        "F1_概念边界": {
            "自造术语次数": jarg,
            "术语首次出现上下文": first_def,
            "问题": "自造术语是否有显式定义，且定义是否在首次使用附近",
        },
        "F2_外行语言": {
            "类比标记": counts,
            "类比总数": ana_total,
            "类比每千字": round(ana_total / chars * 1000, 2),
            "术语每千字": round(sum(jarg.values()) / chars * 1000, 1),
        },
        "F3_卡壳处理": {
            "自我纠正段": self_fix,
            "次数": len(self_fix),
            "悬空提问": dangling,
        },
        "F4_教学闭环": {
            "检验性提问次数": sum(check.values()),
            "检验性提问明细": check,
            "行动指令次数": sum(act.values()),
            "行动指令明细": act,
            "结尾是否含行动指令": action_in_tail,
            "结尾150字": tail[-150:],
        },
    }
    hl = diagram_highlight()
    scores["画面_高亮执行"] = hl
    with open(os.path.join(WORK, "feynman_scores.json"), "w", encoding="utf-8") as fh:
        json.dump(scores, fh, ensure_ascii=False, indent=2)
    log(json.dumps(scores, ensure_ascii=False, indent=2))
    log("=== feynman done")


if __name__ == "__main__":
    main()
