#!/usr/bin/env python3
"""ASR 音轨分片 —— 把一次 20 分钟阻塞转写拆成 N 段，可并行喂给转写服务。

用法：
  # 1) 切片（从已有的 audio_16k.wav 切，不重复解码视频）
  python3 scripts/asr_chunk.py --split
  # 2) 对每片并行调用转写后，把结果放回 work/chunks/xxx.txt
  # 3) 合并（自动修正时间戳偏移，输出标准 asr_raw.txt）
  python3 scripts/asr_chunk.py --merge

时间戳格式与 AudioTranscribe 一致：[M:SS.mmm,M:SS.mmm,0] text
日志：logs/asr_chunk.log
"""
import json
import os
import re
import shutil
import sys
import time

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


WORK = os.path.join(ROOT, "work")
WAV = os.path.join(WORK, "audio_16k.wav")
CHUNKS = os.path.join(WORK, "chunks")
MERGED = os.path.join(WORK, "asr_raw.txt")
LOG = os.path.join(ROOT, "logs", "asr_chunk.log")
MANIFEST = os.path.join(CHUNKS, "manifest.json")
CHUNK_SEC = 300
FFMPEG = (os.environ.get("FEIMAN_FFMPEG") or shutil.which("ffmpeg") or
          "/Users/mac/.workbuddy/binaries/python/versions/3.14.3/lib/"
          "python3.14/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-x86_64-v7.1")


def log(m):
    line = f"[chunk] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def split():
    if not os.path.exists(WAV):
        log(f"FATAL: {WAV} 不存在，先跑 media 阶段")
        return 2
    if os.path.isdir(CHUNKS):
        shutil.rmtree(CHUNKS, ignore_errors=True)
    os.makedirs(CHUNKS, exist_ok=True)
    import wave
    with wave.open(WAV, "rb") as w:
        sr = w.getframerate()
        total = w.getnframes()
    dur = total / sr
    n = int(dur // CHUNK_SEC) + 1
    manifest = {"chunk_sec": CHUNK_SEC, "total_sec": round(dur, 2),
                "sample_rate": sr, "chunks": []}
    t0 = time.time()
    for i in range(n):
        start = i * CHUNK_SEC
        length = min(CHUNK_SEC, dur - start)
        if length <= 0:
            break
        out = os.path.join(CHUNKS, f"chunk_{i:02d}.wav")
        cmd = [FFMPEG, "-y", "-hide_banner", "-v", "error",
               "-ss", str(start), "-t", str(length), "-i", WAV,
               "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out]
        r = __import__("subprocess").run(cmd, capture_output=True, text=True)
        ok = os.path.exists(out) and os.path.getsize(out) > 0
        manifest["chunks"].append({
            "idx": i, "path": out, "offset_sec": start,
            "length_sec": round(length, 2), "ok": ok,
            "transcript_file": os.path.join(CHUNKS, f"chunk_{i:02d}.txt"),
        })
        log(f"  chunk{i:02d} start={start}s len={length:.1f}s ok={ok}")
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    log(f"split done n={len(manifest['chunks'])} wall={time.time()-t0:.1f}s -> {MANIFEST}")
    print(f"CHUNKS_READY {MANIFEST} n={len(manifest['chunks'])}")
    return 0


def _parse_ts(t):
    p = t.split(":")
    if len(p) == 3:
        return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
    return int(p[0]) * 60 + float(p[1])


def _fmt_ts(sec):
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m}:{s:06.3f}"


def merge():
    if not os.path.exists(MANIFEST):
        log(f"FATAL: {MANIFEST} 不存在，先 --split")
        return 2
    man = load_manifest()
    out_lines = []
    missing = []
    for c in man["chunks"]:
        tf = c["transcript_file"]
        if not os.path.exists(tf):
            missing.append(c["idx"])
            continue
        txt = open(tf, encoding="utf-8").read()
        pat = re.compile(r"\[(\d+:\d+\.\d+),(\d+:\d+\.\d+),\d+\]\s*(.*)")
        n = 0
        for m in pat.finditer(txt):
            s = _parse_ts(m.group(1)) + c["offset_sec"]
            e = _parse_ts(m.group(2)) + c["offset_sec"]
            out_lines.append(f"[{_fmt_ts(s)},{_fmt_ts(e)},0] {m.group(3).strip()}")
            n += 1
        log(f"  chunk{c['idx']:02d} offset={c['offset_sec']}s segs={n}")
    if missing:
        log(f"!!! 缺转写的分片: {missing} —— 合并结果不完整")
    out_lines.sort(key=lambda x: _parse_ts(x[1:x.index(",")]))
    with open(MERGED, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out_lines) + "\n")
    log(f"merged {len(out_lines)} segs -> {MERGED}")
    print(f"MERGED {MERGED} segs={len(out_lines)} missing={missing}")
    return 0


def load_manifest():
    with open(MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--split":
        sys.exit(split())
    elif len(sys.argv) > 1 and sys.argv[1] == "--merge":
        sys.exit(merge())
    print(__doc__)
    sys.exit(1)
