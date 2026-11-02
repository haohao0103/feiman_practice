#!/usr/bin/env python3
"""诊断：fact_check 这类"长 payload"的 LLM 调用为什么返回空内容。

复现方式：用失败任务真实的转写 + 合成同等长度的稿子，打一次 API，
把**原始响应的结构**打出来（finish_reason / content 长度 / usage / 耗时）。

用法：python3 scripts/diag_llm_empty.py [转写txt]
"""
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as C          # noqa: E402
from webapp.core import courseware_gen as cg  # noqa: E402

DEFAULT_ASR = os.path.join(
    ROOT, "work", "projects", "video_to_script_20261103_002912_1810f9", "asr.txt")


def probe(cfg, label, base, key, model, payload):
    payload["model"] = model
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}", "api-key": key})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=cfg["llm_timeout"]) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  [{label}] 调用失败 {type(e).__name__}: {str(e)[:120]}")
        print(f"     耗时 {time.time()-t0:.1f}s / 超时设定 {cfg['llm_timeout']}s")
        return None
    dt = time.time() - t0
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = msg.get("content")
    print(f"  [{label}] {dt:.1f}s | finish_reason={ch.get('finish_reason')!r} | "
          f"content 类型={type(content).__name__} 长度="
          f"{len(content) if isinstance(content, str) else 'N/A'} | "
          f"usage={data.get('usage')}")
    if not isinstance(content, str) or not content.strip():
        print(f"     msg 的键: {list(msg.keys())}")
        for k in ("reasoning_content", "reasoning", "thinking"):
            if msg.get(k):
                print(f"     {k} 长度: {len(str(msg[k]))}")
    return data


def main():
    asr_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ASR
    cfg = C.load()
    transcript = open(asr_path, encoding="utf-8").read()
    print(f"转写 {len(transcript)} 字符（{len(cnf(transcript)) if False else transcript.count('字')} 个'字'）")
    # 合成同等长度的稿子（模拟成稿规模）
    script = ("这是一段模拟的讲课稿内容，用来把 payload 撑到和真实 fact_check 一样大。"
              "价格行为学讲的是趋势遇阻、回调、极点、突破趋势线这些事。") * 120
    user = f"【原始转写】\n{transcript}\n\n【生成的讲课稿】\n{script}\n\n请输出纠错 JSON 列表。"
    payload = {"model": None, "messages": [
        {"role": "system", "content": cg._FACT_SYS},
        {"role": "user", "content": user}],
        "temperature": cfg["llm_temperature"], "max_tokens": cfg["llm_max_tokens"]}
    print(f"payload: system {len(cg._FACT_SYS)} + user {len(user)} 字符，"
          f"max_tokens={cfg['llm_max_tokens']}，timeout={cfg['llm_timeout']}s\n")
    for base, key, model, label in C.llm_channels(cfg):
        print(f"通道 {label} ({model})：")
        probe(cfg, label, base, key, model, dict(payload))
        print()


if __name__ == "__main__":
    main()
