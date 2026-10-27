#!/usr/bin/env python3
"""ASR 瓶颈量化（任务 A）。

复刻 webapp 真实 ASR 路径：faster-whisper small / cpu / int8 / beam=1 /
language=zh / vad_filter=True / condition_on_previous_text=False。
对一段 30s 真实讲课语音计时，给出 RTF（实时率）与耗时分解。

用法（必须用 python_asr 解释器，保证与线上同环境）：
  python3 scripts/bench_asr.py 2>&1 | tee logs/bench_asr.log
  # 解释器路径见 webapp/core/config.py 的 python_asr（可用 FEIMAN_PYTHON_ASR 覆盖）
"""
import os
import sys
import time
import subprocess

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod
import webapp.core.pipeline_ops as ops

# 隔离纯推理耗时：强制走本地缓存，不触网（线上 run_asr 走 hf-mirror.com 才能下/读）
os.environ.setdefault("HF_ENDPOINT", cfgmod.load()["hf_endpoint"])
os.environ["HF_HUB_OFFLINE"] = "1"

cfg = cfgmod.load()
SRC = os.path.join(ROOT, "work/audio_16k.wav")
CLIP = os.path.join(ROOT, "work/asr_bench_30s.wav")
OUT = os.path.join(ROOT, "work/asr_bench_30s.txt")
ff = cfg["ffmpeg"]

# 1) 切 30s 真实语音（offset 120s，避开开头静音/片头）
t0 = time.time()
subprocess.run([ff, "-y", "-hide_banner", "-v", "error", "-ss", "120", "-t", "30",
                "-i", SRC, "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", CLIP], check=True)
cut = time.time() - t0
# 16k 单声道 s16le = 32000 字节/秒，精确
dur = os.path.getsize(CLIP) / 32000.0
print(f"clip: {CLIP}")
print(f"切片耗时 {cut:.2f}s ｜ 音频时长 {dur:.2f}s ({dur/60:.2f}min)", flush=True)

# 2) 复刻 run_asr 的真实参数跑 faster-whisper
import faster_whisper  # 必须在 python_asr 环境下（env 已在顶部设好）

print(f"faster_whisper={faster_whisper.__version__} ｜ model={cfg['asr_model']} "
      f"device={cfg['asr_device']} ctype={cfg['asr_compute_type']} beam={cfg['asr_beam_size']}", flush=True)
print(f"CPU 逻辑核数={os.cpu_count()}", flush=True)

t0 = time.time()
model = faster_whisper.WhisperModel(cfg["asr_model"], device=cfg["asr_device"],
                                    compute_type=cfg["asr_compute_type"])
load = time.time() - t0
print(f"MODEL_LOADED {load:.2f}s", flush=True)

t1 = time.time()
segs, info = model.transcribe(CLIP, language="zh", beam_size=cfg["asr_beam_size"],
                              vad_filter=True, condition_on_previous_text=False)
n = sum(1 for _ in segs)
trans = time.time() - t1
print(f"TRANSCRIBE_DONE segs={n} {trans:.2f}s", flush=True)

rtf = trans / dur
print("=" * 56)
print(f"音频时长        : {dur:8.2f}s")
print(f"模型加载        : {load:8.2f}s   (含首次下载则另计)")
print(f"转写推理        : {trans:8.2f}s")
print(f"RTF(实时率)     : {rtf:8.4f}   (<1 比实时快, 越大越慢)")
print(f"推断吞吐        : {dur/trans:8.2f}x 实时")
# 外推：20 分钟(1200s)全程 ASR 预计耗时
print(f"外推 20min 全程 : {rtf*1200:8.1f}s  (RTF × 1200s)")
print("=" * 56)
if load > trans:
    print("耗时主因: 模型加载（若未缓存还需下载 ~464MB）")
else:
    print("耗时主因: 转写推理(CPU 计算) —— 这才是链路瓶颈")
