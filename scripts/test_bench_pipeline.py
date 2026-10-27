#!/usr/bin/env python3
"""无头实测：跑通用化课件流水线，验证效果（可指定任意 B 站视频）。

用法: python3 scripts/test_bench_pipeline.py [B站URL]
默认跑标杆同款 BV1rXXfBiECa。
日志: work/bench_run_<bvid>.log
"""
import os, sys, time, subprocess, shutil

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod
from webapp.core import pipeline_ops as ops
from webapp.core import courseware_gen as cg

cfg = cfgmod.load()
URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.bilibili.com/video/BV1rXXfBiECa/"
BVID = URL.rstrip("/").split("/")[-1].split("?")[0]
VIDEO_DIR = os.path.join(ROOT, "work", f"bench_src_{BVID}")
os.makedirs(VIDEO_DIR, exist_ok=True)
TASK_DIR = os.path.join(ROOT, "work", f"bench_task_{BVID}")
os.makedirs(TASK_DIR, exist_ok=True)
OUT = os.path.join(ROOT, "outputs", f"课件_bench_{BVID}")
os.makedirs(OUT, exist_ok=True)

def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

log(f"=== 目标 {URL} (bvid={BVID}) ===")

# 1) 下载：视频流(avc1) + 音轨 分别下，再合并（本机 ffmpeg 无 HEVC 解码，必须 avc1）
merged = os.path.join(VIDEO_DIR, "video.mp4")
if os.path.exists(merged) and os.path.getsize(merged) > 1000:
    log(f"复用已合并视频 {merged}")
    video = merged
else:
    dl_dir = os.path.join(VIDEO_DIR, "dl")
    os.makedirs(dl_dir, exist_ok=True)
    log("下载视频流(avc1)")
    ops.run_cmd(cfg, [cfg["yt_dlp"], "-f", "bv*[height<=1080][vcodec^=avc1]",
                      "-o", os.path.join(dl_dir, "v.%(ext)s"), URL],
                log=log, timeout=1800)
    vs = [os.path.join(dl_dir, f) for f in os.listdir(dl_dir)
          if f.startswith("v.") and f.endswith((".mp4", ".mkv", ".webm", ".flv"))]
    vstream = max(vs, key=os.path.getsize) if vs else None
    log(f"  视频流: {vstream} ({os.path.getsize(vstream)//1024}KB)" if vstream else "视频流缺失")

    log("下载音轨")
    try:
        ops.run_cmd(cfg, [cfg["yt_dlp"], "-f", "ba/b",
                          "-o", os.path.join(dl_dir, "a.%(ext)s"), URL],
                    log=log, timeout=1800)
    except Exception as e:
        log(f"  音轨单独下载失败（{e}）")
    as_ = [os.path.join(dl_dir, f) for f in os.listdir(dl_dir)
           if f.startswith("a.") and f.endswith((".m4a", ".mp3", ".aac", ".wav", ".m4s"))]
    audio = max(as_, key=os.path.getsize) if as_ else None
    log(f"  音轨: {audio} ({os.path.getsize(audio)//1024}KB)" if audio else "无独立音轨")

    if vstream and audio:
        ops.run_cmd(cfg, [cfg["ffmpeg"], "-y", "-hide_banner", "-v", "error",
                          "-i", vstream, "-i", audio,
                          "-c:v", "copy", "-c:a", "aac", "-shortest", merged], log=log)
        log("  已合并音视频 → video.mp4")
    elif vstream:
        shutil.copy2(vstream, merged)
        log("  仅视频流（无独立音轨）")
    else:
        raise RuntimeError("视频下载失败")
    video = merged
log(f"视频: {video}  ({os.path.getsize(video)//1024}KB)")

# 2) 抽取音轨 + ASR（转写确定性，可缓存复用）
wav = os.path.join(TASK_DIR, "audio.wav")
asr_txt = os.path.join(TASK_DIR, "asr.txt")
if os.path.exists(asr_txt) and os.path.getsize(asr_txt) > 200:
    log("复用已有 ASR 转写")
else:
    ops.extract_audio(cfg, video, wav, log=log)
    ops.run_asr(cfg, wav, asr_txt, log=log, workdir=TASK_DIR)
segs = ops.parse_asr_file(asr_txt)
transcript = ops.asr_to_text(segs)
log(f"ASR {len(segs)} 段, 转写 {len(transcript)} 字")

# 时长
dur = ops.ffprobe_duration(cfg, video)
log(f"时长: {dur:.1f}s")

# 3) 跑通用化课件流水线
# 注意：task_dir 与 out_dir 都传 OUT，保证 frames_clean/diagrams/MD/PDF 同目录，
# 相对路径 frames_clean/Vxxx.jpg 在 PDF 里能正确解析（否则抽帧落到了别处，PDF 会丢图）。
log("=== 调 courseware_gen.build_courseware ===")
res = cg.build_courseware(cfg, video, transcript, dur, OUT, OUT, log=log)

log("=== 结果 ===")
log(f"成稿字数: {res['script_chars']}")
log(f"原讲义图: {len(res['frame_manifest'])} 张")
log(f"自制图:   {len(res['diagram_manifest'])} 张")
log(f"纠错条数: {len(res['corrections'])}")
log(f"总画面:   {res['total_images']}")
log(f"PDF: {res['pdf_path']}")
log(f"MD:  {res['script_path']}")
if res["corrections"]:
    for c in res["corrections"]:
        log(f"  纠错: [{c.get('issue')}] {c.get('original','')[:30]} -> {c.get('correction','')[:30]}")
