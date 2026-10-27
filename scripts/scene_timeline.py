#!/usr/bin/env python3
"""帧间差分 -> 画面切换时间线。用于核验"自制图A全程主画面"承诺是否兑现。

输出：work/scene_timeline.json
日志：logs/scene_timeline.log
"""
import json
import os

import numpy as np
from PIL import Image

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


FRAMES = os.path.join(ROOT, "work", "frames")
WORK = os.path.join(ROOT, "work")
LOG = os.path.join(ROOT, "logs", "scene_timeline.log")
INTERVAL = 10


def log(m):
    line = f"[scene] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_small(p, w=480):
    im = Image.open(p).convert("RGB").resize((w, int(w * 2240 / 3584)))
    return np.asarray(im).astype(np.int16)


def main():
    files = sorted(f for f in os.listdir(FRAMES)
                   if f.endswith(".jpg") and os.path.isfile(os.path.join(FRAMES, f)))
    prev = None
    changes = []
    diffs = []
    for i, f in enumerate(files):
        a = load_small(os.path.join(FRAMES, f))
        if prev is not None:
            d = float(np.abs(a - prev).mean())
            diffs.append({"idx": i + 1, "sec": (i + 1) * INTERVAL, "diff": round(d, 3)})
            if d > 3.0:
                changes.append({"idx": i + 1, "sec": (i + 1) * INTERVAL, "diff": round(d, 3)})
        prev = a
    # 静止判定：连续 diff < 0.6 视为画面完全没动
    still = [d for d in diffs if d["diff"] < 0.6]
    moving = [d for d in diffs if d["diff"] >= 0.6]
    out = {
        "n_frames": len(files),
        "n_changes": len(changes),
        "changes": changes,
        "n_static_pairs": len(still),
        "static_ratio": round(len(still) / len(diffs), 3),
        "n_moving_pairs": len(moving),
        "diffs": diffs,
    }
    with open(os.path.join(WORK, "scene_timeline.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    log(f"frames={len(files)} changes={len(changes)} static_pairs={len(still)}/{len(diffs)}")
    log("--- change points ---")
    for c in changes:
        log(f"  t={c['sec']:>5}s  idx={c['idx']:>4}  diff={c['diff']}")
    log("=== scene done")


if __name__ == "__main__":
    main()
