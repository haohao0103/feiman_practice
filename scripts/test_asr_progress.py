#!/usr/bin/env python3
"""验证 run_asr 的 progress_cb 会随转写推进多次回调且 frac 递增。"""
import os
import sys

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)
# 隔离网络：用本地缓存模型，避免 HF 镜像不可达时子进程失败（仅测试用）
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
from webapp.core import config as cfgmod
import webapp.core.pipeline_ops as ops

cfg = cfgmod.load()
WAV = os.path.join(ROOT, "work/asr_bench_30s.wav")
assert os.path.exists(WAV), "缺少测试音频 work/asr_bench_30s.wav"

fracs = []
def cb(f):
    fracs.append(round(f, 3))

out = os.path.join(ROOT, "work/_asr_progress_test.txt")
ops.run_asr(cfg, WAV, out, log=lambda m: None, workdir=os.path.join(ROOT, "work"),
            progress_cb=cb)

print(f"回调次数: {len(fracs)}")
print(f"首 frac={fracs[0] if fracs else None}  末 frac={fracs[-1] if fracs else None}")
inc = all(fracs[i] <= fracs[i+1] + 1e-9 for i in range(len(fracs)-1))
print(f"单调递增: {inc}")
print(f"末值≈1.0: {abs(fracs[-1]-1.0) < 0.05 if fracs else False}")
print(f"采样: {fracs[:6]} ... {fracs[-3:]}")
assert len(fracs) >= 3, "progress_cb 回调次数过少，进度条仍会卡住"
assert inc, "frac 未单调递增"
assert abs(fracs[-1] - 1.0) < 0.05, "末值未到 1.0"
print("✅ ASR 过程进度上报正常（进度条会在转写期间持续移动）")
