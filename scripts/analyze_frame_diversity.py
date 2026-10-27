#!/usr/bin/env python3
"""量化「画面内容多样性」，为自适应抽帧定阈值（不拍脑袋）。

思路：一段视频里大量帧其实是**同一屏画面**（只是光标/标注在动）。
人类挑帧的本质 = 只在"视觉上确实是另一张画面"时多留一张。
所以先用视觉指纹算两两距离，再看距离分布上有没有天然分层，
据此定一个「多远算不同画面」的阈值 TH。

指纹 = 32x32 灰度缩略 MAD（0.7 权重）+ 16 桶色相直方图 L1 距离（0.3 权重）。
输出：各样本集的两两距离分布 + 不同 TH 下的「视觉状态数」曲线。
"""
import os
import sys
from collections import defaultdict

import numpy as np
from PIL import Image

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


SAMPLES = [
    ("图表密集-23张(BV1rXXfBiECa 段帧)", os.path.join(ROOT, "work/bench_task/frames_clean")),
    ("图表密集-50张(场景切分)", os.path.join(ROOT, "work/projects/video_to_script_20261026_163334_bc330f/frames_clean")),
    ("静态口播-3张(BV1Nw9MBYEsz)", os.path.join(ROOT, "work/projects/video_to_script_20261026_202703_f9a46e/frames_clean")),
]
THS = [0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.08, 0.12]


def signature(path, size=32):
    im = Image.open(path).convert("RGB").resize((size, size), Image.BILINEAR)
    g = np.asarray(im.convert("L"), dtype=np.float32) / 255.0
    hsv = np.asarray(im.convert("HSV"), dtype=np.float32)
    hist = np.histogram(hsv[..., 0], bins=16, range=(0, 256))[0].astype(np.float32)
    hist = hist / max(1.0, hist.sum())
    return g, hist


def dist(a, b):
    g1, h1 = a
    g2, h2 = b
    mad = float(np.abs(g1 - g2).mean())
    l1 = float(np.abs(h1 - h2).sum()) / 2.0
    return 0.7 * mad + 0.3 * l1


def components(sigs, th):
    """距离 < th 视为同一画面 → 并查集求连通分量数 = 视觉状态数。"""
    n = len(sigs)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if dist(sigs[i], sigs[j]) < th:
                a, b = find(i), find(j)
                if a != b:
                    parent[a] = b
    return len({find(i) for i in range(n)})


def main():
    print("=" * 78)
    print("画面内容多样性分析 —— 用于确定自适应抽帧阈值 TH")
    print("=" * 78)
    for name, d in SAMPLES:
        if not os.path.isdir(d):
            print(f"\n[跳过] {name}  目录不存在: {d}")
            continue
        files = sorted(f for f in os.listdir(d) if f.lower().endswith((".jpg", ".png")))
        if not files:
            print(f"\n[跳过] {name}  无图片")
            continue
        sigs = [signature(os.path.join(d, f)) for f in files]
        n = len(sigs)
        pair = []
        for i in range(n):
            for j in range(i + 1, n):
                pair.append(dist(sigs[i], sigs[j]))
        pair.sort()
        print(f"\n{'-' * 78}")
        print(f"样本: {name}   帧数={n}")
        print(f"两两距离: min={pair[0]:.5f} p10={pair[int(len(pair)*0.1)]:.5f} "
              f"中位={pair[len(pair)//2]:.5f} p90={pair[int(len(pair)*0.9)]:.5f} max={pair[-1]:.5f}")
        print("  最小 12 对(最相似=大概率同一屏):")
        for i in range(min(12, len(pair))):
            print(f"    {pair[i]:.5f}")
        print("  视觉状态数随阈值变化（连通分量数）:")
        row = []
        for th in THS:
            c = components(sigs, th)
            row.append(f"TH={th:<6}→{c:>3} 张")
        for i in range(0, len(row), 4):
            print("    " + "   ".join(row[i:i + 4]))
        # 每帧到最近邻的距离分布（判断"同屏簇"是否紧密成团）
        nn = []
        for i in range(n):
            best = min(dist(sigs[i], sigs[j]) for j in range(n) if j != i) if n > 1 else 0
            nn.append(best)
        nn.sort()
        print(f"  最近邻距离: min={nn[0]:.5f} 中位={nn[len(nn)//2]:.5f} max={nn[-1]:.5f}")
    print("\n" + "=" * 78)
    print("解读: TH 取在「同屏簇内距离」之上、「不同画面之间距离」之下 → 状态数即为内容自适应的帧数")


if __name__ == "__main__":
    main()
