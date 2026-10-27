#!/usr/bin/env python3
"""探测小米 MiMo /chat/completions 是否支持 SSE stream。"""
import os
import sys
import json
import time

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)
from webapp.core import config as cfgmod
import urllib.request

cfg = cfgmod.load()
channels = cfgmod.llm_channels(cfg)
base, key, model, label = channels[0]
print(f"channel={label} model={model} base={base}")

payload = {
    "model": model,
    "messages": [{"role": "system", "content": "你只回复'好的'两个字，不要多余内容"},
                 {"role": "user", "content": "hi"}],
    "temperature": 0.7, "max_tokens": 2000, "stream": True,
}
req = urllib.request.Request(
    base + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json",
             "Authorization": f"Bearer {key}", "api-key": key})

t0 = time.time()
n, chars = 0, 0
with urllib.request.urlopen(req, timeout=60) as r:
    for raw in r:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            print("收到 [DONE]"); break
        try:
            j = json.loads(data)
            delta = j["choices"][0]["delta"].get("content", "")
            if delta:
                chars += len(delta); n += 1
                if n <= 5:
                    print("CHUNK", repr(delta))
        except Exception as e:
            print("parse-err", e, repr(data[:80]))
print(f"结果: chunks={n} chars={chars} time={time.time()-t0:.1f}s")
print("✅ 支持 SSE" if n > 0 else "❌ 未收到任何增量（可能不支持 stream）")
