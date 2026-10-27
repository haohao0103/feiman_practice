#!/usr/bin/env python3
"""原课程视频画面切分 —— 为课件逐字稿提供"哪些时刻该用哪张画面"。

方法：
  1. 场景检测（帧间差分 + 直方图相似度）找出讲义页/图表的切换点
  2. 对每个场景取中位帧（避免讲师动画/鼠标移动的干扰）
  3. 输出每个场景的时间区间 + 代表帧，供逐字稿按时间戳挂画面

输出：work/orig_scenes.json
      work/orig_frames/scene_XX.jpg
日志：logs/orig_scene_split.log
"""
import shutil
import json
import os
import subprocess
import time

import numpy as np
from PIL import Image

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


FFMPEG = (os.environ.get("FEIMAN_FFMPEG") or shutil.which("ffmpeg") or
          "/Users/mac/.workbuddy/binaries/python/versions/3.14.3/lib/"
          "python3.14/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-x86_64-v7.1")
SRC = os.path.join(ROOT, "work", "src_video", "BV1rXXfBiECa_1080p.mp4")
OUT_FRAMES = os.path.join(ROOT, "work", "orig_frames")
OUT_JSON = os.path.join(ROOT, "work", "orig_scenes.json")
LOG = os.path.join(ROOT, "logs", "orig_scene_split.log")

PROBE_INTERVAL = 1.0   # 场景检测采样间隔（秒）
SCENE_TH = 0.06        # 场景切换阈值（归一化差）


def log(m):
    line = f"[scene] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def probe_duration():
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", SRC],
                       capture_output=True, text=True, errors="replace")
    import re
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", r.stderr)
    if not m:
        raise RuntimeError("无法解析时长")
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


def extract_probe_frames(dur):
    """按 1 秒间隔抽小图做场景检测。"""
    tmp = os.path.join(ROOT, "work", "_probe_frames")
    os.makedirs(tmp, exist_ok=True)
    cmd = [FFMPEG, "-y", "-hide_banner", "-v", "error", "-i", SRC,
           "-vf", f"fps=1/{PROBE_INTERVAL},scale=320:-2",
           os.path.join(tmp, "p_%05d.jpg")]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    files = sorted(f for f in os.listdir(tmp) if f.endswith(".jpg"))
    log(f"probe frames={len(files)} wall={time.time()-t0:.1f}s")
    return tmp, files


def detect_scenes(tmp, files):
    """返回场景边界列表 [(start_idx, end_idx)]"""
    arrs = []
    for f in files:
        a = np.asarray(Image.open(os.path.join(tmp, f)).convert("L"),
                       dtype=np.float32) / 255.0
        arrs.append(a)
    diffs = []
    for i in range(1, len(arrs)):
        d = float(np.abs(arrs[i] - arrs[i - 1]).mean())
        diffs.append((i, d))
    bounds = [0]
    for idx, d in diffs:
        if d > SCENE_TH:
            bounds.append(idx)
    bounds.append(len(arrs))
    scenes = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a >= 2:      # 至少 2 秒才算一个场景
            scenes.append((a, b))
    log(f"检测到 {len(scenes)} 个场景（阈值 {SCENE_TH}）")
    return scenes


def extract_scene_frames(scenes):
    """为每个场景抽一张代表帧（取中点）。"""
    os.makedirs(OUT_FRAMES, exist_ok=True)
    dur = probe_duration()
    for f in os.listdir(OUT_FRAMES):
        os.remove(os.path.join(OUT_FRAMES, f))
    results = []
    t0 = time.time()
    for i, (a, b) in enumerate(scenes, 1):
        t_a = a * PROBE_INTERVAL
        t_b = min(dur, b * PROBE_INTERVAL)
        t_mid = (t_a + t_b) / 2
        out = os.path.join(OUT_FRAMES, f"scene_{i:02d}.jpg")
        cmd = [FFMPEG, "-y", "-hide_banner", "-v", "error",
               "-ss", f"{t_mid:.2f}", "-i", SRC,
               "-frames:v", "1", "-q:v", "2", out]
        subprocess.run(cmd, capture_output=True, text=True)
        ok = os.path.exists(out)
        results.append({
            "idx": i,
            "start_sec": round(t_a, 1),
            "end_sec": round(t_b, 1),
            "duration_sec": round(t_b - t_a, 1),
            "mid_sec": round(t_mid, 2),
            "frame": out if ok else None,
        })
    log(f"场景代表帧抽取完成 wall={time.time()-t0:.1f}s")
    return results


def main():
    log("=== orig scene split start")
    dur = probe_duration()
    log(f"duration={dur:.1f}s  ({dur/60:.2f} min)")
    tmp, files = extract_probe_frames(dur)
    scenes = detect_scenes(tmp, files)
    results = extract_scene_frames(scenes)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump({"source": SRC, "duration_sec": round(dur, 2),
                   "scenes": results}, fh, ensure_ascii=False, indent=1)
    log("--- 场景时间轴 ---")
    for r in results:
        log(f"  scene{r['idx']:02d}  {r['start_sec']:>6.1f}s - {r['end_sec']:>6.1f}s "
            f"({r['duration_sec']:>5.1f}s) mid={r['mid_sec']}s")
    log("=== orig scene split done")


if __name__ == "__main__":
    main()
