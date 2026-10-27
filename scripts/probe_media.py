#!/usr/bin/env python3
"""媒体抽帧/抽音轨 —— 优化版。

原版瓶颈：ffmpeg 用 fps=1/10 滤镜逐帧解码整段 60fps 视频（20 分钟×60fps=72000 帧），
            实际只需 121 帧，浪费了 99.8% 的解码量。

优化手段：
  1. -skip_frame nokey  —— 只解关键帧，跳过 P/B 帧解码（关键路径）
  2. -ss + -t 分段并行 —— 把视频切成 N 段并行抽帧（多进程）
  3. 音画分离 —— 音频单独一条命令，与抽帧并行
  4. 音轨降采样直接出 wav，不做二次转码

输出：work/audio_16k.wav, work/frames/f_XXXX.jpg, work/probe_meta.json
日志：logs/probe_media.log
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


FFMPEG = (os.environ.get("FEIMAN_FFMPEG") or shutil.which("ffmpeg") or
          "/Users/mac/.workbuddy/binaries/python/versions/3.14.3/lib/"
          "python3.14/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-x86_64-v7.1")
SRC = os.environ.get("FEIMAN_VIDEO", "")   # 填待分析的录屏路径
WORK = os.path.join(ROOT, "work")
FRAMES = os.path.join(WORK, "frames")
WAV = os.path.join(WORK, "audio_16k.wav")
META = os.path.join(WORK, "probe_meta.json")
LOG = os.path.join(ROOT, "logs", "probe_media.log")

FRAME_INTERVAL = 10
FRAME_WIDTH = 1400
SEG_WORKERS = 6          # 并行分段数


def log(msg):
    line = f"[probe] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def probe():
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", SRC],
                       capture_output=True, text=True, errors="replace")
    txt = r.stderr
    meta = {"src": SRC}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", txt)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        meta["duration_sec"] = h * 3600 + mi * 60 + s
    for key, pat in (("video_stream", r"Stream #\d+.*Video:.*"),
                     ("audio_stream", r"Stream #\d+.*Audio:.*")):
        mm = re.search(pat, txt)
        if mm:
            meta[key] = mm.group(0).strip()
    return meta


def extract_audio():
    cmd = [FFMPEG, "-y", "-hide_banner", "-v", "error", "-i", SRC,
           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", WAV]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    ok = os.path.exists(WAV) and os.path.getsize(WAV) > 0
    log(f"audio extract ok={ok} size={os.path.getsize(WAV) if ok else 0}")
    return ok


def _extract_segment(args):
    """单段抽帧：-ss 快速定位 + 只解关键帧。"""
    seg_idx, seg_start, seg_dur = args
    outdir = os.path.join(FRAMES, f"_seg{seg_idx}")
    os.makedirs(outdir, exist_ok=True)
    cmd = [FFMPEG, "-y", "-hide_banner", "-v", "error",
           "-ss", str(seg_start), "-t", str(seg_dur),
           "-skip_frame", "nokey",
           "-i", SRC,
           "-vf", f"fps=1/{FRAME_INTERVAL},scale={FRAME_WIDTH}:-2",
           "-q:v", "3", os.path.join(outdir, "s_%05d.jpg")]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    n = len([f for f in os.listdir(outdir) if f.endswith(".jpg")])
    return seg_idx, n, time.time() - t0, r.returncode


def _clear_frames_dir():
    """彻底清空帧目录：先删所有文件，再删所有子目录（含残留 _segN）。
    注意：os.rmdir 在 WorkBuddy 沙箱下对非空目录会报 EISDIR，必须用 shutil.rmtree。"""
    os.makedirs(FRAMES, exist_ok=True)
    for entry in os.listdir(FRAMES):
        p = os.path.join(FRAMES, entry)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.isfile(p):
            os.remove(p)


def extract_frames(duration):
    _clear_frames_dir()

    seg_len = max(60, duration / SEG_WORKERS)
    jobs = []
    i = 0
    while i * seg_len < duration:
        jobs.append((i, i * seg_len, seg_len))
        i += 1

    t0 = time.time()
    total = 0
    # 段内串行、段间并行：用线程池（ffmpeg 是子进程，线程足够）
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=SEG_WORKERS) as ex:
        for seg_idx, n, dt, rc in ex.map(_extract_segment, jobs):
            total += n
            log(f"  seg{seg_idx} -> {n} frames  {dt:.1f}s  rc={rc}")
    log(f"frames extracted total={total}  parallel_wall={time.time()-t0:.1f}s")

    # 按时间顺序重命名：seg_idx * 每段帧数 + 段内序号
    index = []
    for seg_idx, _, _ in jobs:
        sd = os.path.join(FRAMES, f"_seg{seg_idx}")
        if not os.path.isdir(sd):
            continue
        for f in sorted(os.listdir(sd)):
            if f.endswith(".jpg"):
                index.append((seg_idx, os.path.join(sd, f)))
    # 各段帧数按顺序累加
    counts = {}
    for seg_idx, _ in index:
        counts[seg_idx] = counts.get(seg_idx, 0) + 1
    base = {}
    acc = 0
    for seg_idx in sorted(counts):
        base[seg_idx] = acc
        acc += counts[seg_idx]
    out_i = 1
    for seg_idx, path in index:
        dst = os.path.join(FRAMES, f"f_{out_i:04d}.jpg")
        os.replace(path, dst)
        out_i += 1
    # 清理空段目录
    for d in os.listdir(FRAMES):
        if d.startswith("_seg"):
            p = os.path.join(FRAMES, d)
            shutil.rmtree(p, ignore_errors=True)
    log(f"frames renamed to f_0001..f_{out_i-1:04d}  total={out_i-1}")
    return out_i - 1


def main():
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    log(f"=== probe start (optimized) {SRC}")
    t0 = time.time()
    meta = probe()
    dur = meta.get("duration_sec", 0)
    log(f"duration_sec={dur}")

    # 音画并行
    with ProcessPoolExecutor(max_workers=2) as ex:
        f_audio = ex.submit(extract_audio)
        f_frames = ex.submit(extract_frames, dur)
        extract_audio_res = f_audio.result()
        n = f_frames.result()
    log(f"audio={extract_audio_res}  frames={n}")

    meta["frame_interval_sec"] = FRAME_INTERVAL
    meta["frames_extracted"] = n
    meta["probe_wall_sec"] = round(time.time() - t0, 1)
    with open(META, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    log(f"meta written {META}  wall={meta['probe_wall_sec']}s")
    log("=== probe done")
    print("PROBE_DONE", json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
