#!/usr/bin/env python3
"""音频洁净度分析：底噪、静音比、疑似非语音脉冲（键盘/系统音/口误杂音）。

输出：work/audio_quality.json
日志：logs/audio_quality.log
"""
import json
import os
import wave

import numpy as np

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


WORK = os.path.join(ROOT, "work")
WAV = os.path.join(WORK, "audio_16k.wav")
LOG = os.path.join(ROOT, "logs", "audio_quality.log")


def log(m):
    line = f"[audio] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    with wave.open(WAV, "rb") as w:
        n, sr, ch = w.getnframes(), w.getframerate(), w.getnchannels()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    dur = len(a) / sr
    win = int(0.02 * sr)
    frames = len(a) // win
    rms = np.sqrt((a[:frames * win].reshape(frames, win) ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    noise_floor = float(np.percentile(db, 5))
    speech_level = float(np.percentile(db, 95))
    silence_ratio = float((db < noise_floor + 6).mean())

    # 疑似非语音脉冲：短时高能量且孤立（前后 200ms 都是低能量）
    hot = np.where(db > speech_level - 6)[0]
    impulses = []
    if len(hot):
        groups = np.split(hot, np.where(np.diff(hot) > 5)[0] + 1)
        for g in groups:
            if len(g) < 2:
                continue
            s, e = g[0], g[-1]
            pre = db[max(0, s - 10):s].mean() if s > 0 else noise_floor
            post = db[e + 1:e + 11].mean() if e + 11 < len(db) else noise_floor
            if pre < speech_level - 18 and post < speech_level - 18:
                impulses.append({"t": round(s * 0.02, 2),
                                 "dur_ms": int((e - s + 1) * 20),
                                 "peak_db": round(float(db[s:e + 1].max()), 1)})
    out = {
        "duration_sec": round(dur, 1),
        "sample_rate": sr,
        "channels": ch,
        "noise_floor_db": round(noise_floor, 1),
        "speech_level_db": round(speech_level, 1),
        "snr_db": round(speech_level - noise_floor, 1),
        "silence_ratio": round(silence_ratio, 3),
        "impulse_count": len(impulses),
        "impulses_top20": sorted(impulses, key=lambda x: -x["peak_db"])[:20],
        "clipped_samples": int((np.abs(a) > 0.995).sum()),
    }
    with open(os.path.join(WORK, "audio_quality.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    log(json.dumps(out, ensure_ascii=False, indent=2))
    log("=== audio done")


if __name__ == "__main__":
    main()
