#!/usr/bin/env python3
"""画面构成统计 + 讲稿结构分段（重复定位）。

1) 逐帧统计暗像素占比（TradingView 深色主题 = 暗占比高），
   区分"讲义文字页"与"K线图页"的时间分布。
2) 检测是否出现"自制流程图在视口内"(通过彩色像素饱和度聚类)，
   统计流程图在画面中的占比与连续区间。
3) 把 ASR 切成语义段，标注"五步"相关重复出现的区间。

输出：work/visual_stats.json, work/structure.json
日志：logs/visual_structure.log
"""
import json
import os
import re

import numpy as np
from PIL import Image

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


FRAMES = os.path.join(ROOT, "work", "frames")
WORK = os.path.join(ROOT, "work")
LOG = os.path.join(ROOT, "logs", "visual_structure.log")
INTERVAL = 10

# 自制流程图的强调色（红/橙/蓝/绿），用于判定"图在视口内"
TARGET_HUES = [(345, 20), (18, 45), (200, 245), (95, 160)]


def log(m):
    line = f"[visual] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def hue_in_ranges(h, ranges):
    for lo, hi in ranges:
        if lo <= h <= hi:
            return True
    return False


def frame_stats(path):
    im = Image.open(path).convert("RGB")
    im = im.resize((560, 350))
    a = np.asarray(im).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    dark_ratio = float((mx < 90).mean())
    sat = (mx - mn)
    sat = np.where(mx > 0, sat / np.maximum(mx, 1), 0)
    colored = float((sat > 0.45).mean())
    # 色相
    import colorsys
    colored_hue = 0.0
    colored_total = 0
    step = 3
    for y in range(0, a.shape[0], step):
        for x in range(0, a.shape[1], step):
            rr, gg, bb = int(r[y, x]), int(g[y, x]), int(b[y, x])
            h, s, v = colorsys.rgb_to_hsv(rr / 255, gg / 255, bb / 255)
            hd = h * 360
            if s > 0.45 and v > 0.35:
                colored_total += 1
                if hue_in_ranges(hd, TARGET_HUES):
                    colored_hue += 1
    hue_ratio = colored_hue / max(colored_total, 1)
    return {
        "dark_ratio": round(dark_ratio, 4),
        "colored_ratio": round(colored, 4),
        "hue_hit_ratio": round(hue_ratio, 4),
    }


def visual():
    files = sorted(f for f in os.listdir(FRAMES)
                   if f.endswith(".jpg") and os.path.isfile(os.path.join(FRAMES, f)))
    rows = []
    for i, f in enumerate(files):
        s = frame_stats(os.path.join(FRAMES, f))
        s["idx"] = i + 1
        s["sec"] = (i + 1) * INTERVAL
        rows.append(s)
    dark = [r for r in rows if r["dark_ratio"] > 0.25]
    light = [r for r in rows if r["dark_ratio"] <= 0.25]
    # 流程图在视口：色相命中率高 且 总体彩色像素够多
    diagram = [r for r in rows if r["hue_hit_ratio"] > 0.55 and r["colored_ratio"] > 0.008]
    tv = [r for r in rows if r["dark_ratio"] > 0.25]
    out = {
        "total_frames": len(rows),
        "kline_dark_frames": len(tv),
        "kline_dark_sec": len(tv) * INTERVAL,
        "doc_light_frames": len(light),
        "doc_light_sec": len(light) * INTERVAL,
        "diagram_visible_frames": len(diagram),
        "diagram_visible_sec": len(diagram) * INTERVAL,
        "diagram_ratio": round(len(diagram) / len(rows), 3),
        "kline_intervals_sec": [
            [tv[0]["sec"], tv[-1]["sec"]] if tv else None,
        ],
        "diagram_intervals_sec": [],
        "rows": rows,
    }
    # 区间归并
    def merge(idxs):
        out_ = []
        for r in idxs:
            if out_ and r["sec"] - out_[-1][1] <= INTERVAL * 1.5:
                out_[-1][1] = r["sec"]
            else:
                out_.append([r["sec"], r["sec"]])
        return out_
    out["diagram_intervals_sec"] = merge(diagram)
    out["kline_intervals_merged"] = merge(tv)
    with open(os.path.join(WORK, "visual_stats.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    log(json.dumps({k: v for k, v in out.items() if k != "rows"}, ensure_ascii=False, indent=1))
    return out


def structure():
    segs = json.load(open(os.path.join(WORK, "asr_segments.json"), encoding="utf-8"))
    # 关键词锚点
    anchors = {
        "五步总纲_第一遍": [(58, 60), (180, 185)],
        "五步重讲_第二遍": [(300, 305), (520, 528)],
        "五步重讲_第三遍": [(518, 525), (660, 700)],
        "三归宿_第一遍": [(177, 185), (295, 300)],
        "三归宿_第二遍": [(665, 675), (980, 985)],
        "例1_K线走五步": [(980, 990), (1200, 1205)],
    }
    # 统计每 60 秒窗口内出现"第N步"的次数 -> 定位重复
    windows = []
    for t0 in range(0, 1200, 60):
        w = [s["text"] for s in segs if t0 <= s["start"] < t0 + 60]
        txt = "".join(w)
        steps = len(re.findall(r"第[一二三四五]步|第[1-5]步", txt))
        windows.append({
            "window": f"{t0//60}-{(t0+60)//60}min",
            "chars": len(re.findall(r"[\u4e00-\u9fff]", txt)),
            "step_mentions": steps,
            "trend_mentions": txt.count("趋势"),
        })
    # 关键句：自我纠正 / 卡壳
    markers = []
    pat = re.compile(r"(讲错|不对|不不|搞错|我们刚才|等等|这个什么意思|跟上|明白了吗|OK到这里|快速进入)")
    for s in segs:
        if pat.search(s["text"]):
            markers.append([s["start"], s["text"][:80]])
    out = {"anchors": anchors, "windows": windows, "markers": markers}
    with open(os.path.join(WORK, "structure.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    log(f"windows={len(windows)} markers={len(markers)}")
    for w in windows:
        log(f"  {w['window']}  chars={w['chars']} step_mentions={w['step_mentions']} trend={w['trend_mentions']}")
    return out


def main():
    visual()
    structure()
    log("=== visual+structure done")


if __name__ == "__main__":
    main()
