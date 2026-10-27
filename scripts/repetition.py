#!/usr/bin/env python3
"""精确定位"五步骤"与"三种归宿"的重复讲解区间（避免凭印象下结论）。

方法：把 ASR 按关键词锚点切成段，统计每个语义单元（遇阻/测试极值/破趋势线/
破前高低点/高低点上移下移）的**独立讲解轮次**（相邻两次出现间隔 > 90s 记为新一轮）。
输出每轮的起止时间与字数，以及总重复字数占比。

输出：work/repetition.json
日志：logs/repetition.log
"""
import json
import os
import re

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


WORK = os.path.join(ROOT, "work")
LOG = os.path.join(ROOT, "logs", "repetition.log")
CN = re.compile(r"[\u4e00-\u9fff]")


def log(m):
    line = f"[rep] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


STEPS = {
    "S1_遇阻": ["遇阻"],
    "S2_测试极值点": ["极值", "测试"],
    "S3_破趋势线": ["趋势线"],
    "S4_破前高前低": ["前高", "前低", "结构支点"],
    "S5_高低点上移下移": ["上移", "下移"],
    "三归宿": ["交易区间", "深度回调", "三种归宿", "三种结果"],
}


def main():
    segs = json.load(open(os.path.join(WORK, "asr_segments.json"), encoding="utf-8"))
    for k, pats in STEPS.items():
        hits = []
        for s in segs:
            t = s["text"]
            if any(p in t for p in pats):
                hits.append(s["start"])
        # 聚成"讲解轮次"
        rounds = []
        for h in hits:
            if rounds and h - rounds[-1][-1] <= 90:
                rounds[-1].append(h)
            else:
                rounds.append([h])
        log(f"--- {k}: hits={len(hits)} rounds={len(rounds)}")
        for i, r in enumerate(rounds, 1):
            log(f"      round{i}: {r[0]:.0f}s -> {r[-1]:.0f}s  (span {r[-1]-r[0]:.0f}s)")

    # 语义块级重复检测：滑窗 90s，计算同窗口内与全片其它窗口的最高重合
    win = 90
    texts = []
    for s in segs:
        texts.append((s["start"], s["end"], re.sub(r"[^一-鿿]", "", s["text"])))
    windows = []
    for t0 in range(0, 1200, win):
        chunk = "".join(t for st, en, t in texts if st >= t0 and st < t0 + win)
        if len(chunk) >= 20:
            windows.append({"t0": t0, "t1": t0 + win, "txt": chunk})
    # 找最高重合对（用 4-gram 集合 Jaccard）
    def grams(t, n=4):
        return set(t[i:i + n] for i in range(max(1, len(t) - n + 1)))
    gs = [grams(w["txt"]) for w in windows]
    pairs = []
    for i in range(len(windows)):
        for j in range(i + 1, len(windows)):
            inter = len(gs[i] & gs[j])
            if inter:
                jac = inter / min(len(gs[i]), len(gs[j]))
                if jac >= 0.35:
                    pairs.append({
                        "a": [windows[i]["t0"], windows[i]["t1"]],
                        "b": [windows[j]["t0"], windows[j]["t1"]],
                        "jaccard": round(jac, 3),
                        "chars_a": len(windows[i]["txt"]),
                        "chars_b": len(windows[j]["txt"]),
                    })
    pairs.sort(key=lambda x: -x["jaccard"])
    log(f"--- near-duplicate window pairs (Jaccard>=0.35): {len(pairs)}")
    for p in pairs[:20]:
        log(f"      {p['a']} ~ {p['b']}  J={p['jaccard']}  chars={p['chars_a']}/{p['chars_b']}")

    out = {"pairs": pairs}
    with open(os.path.join(WORK, "repetition.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    log("=== repetition done")


if __name__ == "__main__":
    main()
