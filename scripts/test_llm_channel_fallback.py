#!/usr/bin/env python3
"""测试：LLM 双通道容错 —— 空内容必须**继续试下一个通道**。

★ 实测踩过的坑：长 payload 的调用偶发返回 `content: ""`，
旧实现直接 `return ""` → 备用通道**根本没被试** → 上层报的"双通道均不可用"是假的
→ 事实核查失败 → 20 分钟的成稿流水线整个炸掉。

用法：python3 scripts/test_llm_channel_fallback.py
"""
import io
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod       # noqa: E402
from webapp.core import pipeline_ops as ops   # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


class FakeResp:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_opener(responses):
    """responses: 每次调用依次返回的 payload dict（抛异常表示该通道失败）。"""
    seq = list(responses)

    def fake_urlopen(req, timeout=None):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)

    return fake_urlopen


def payload_of(content, finish="stop"):
    return {"choices": [{"message": {"content": content},
                         "finish_reason": finish}]}


def main():
    cfg = cfgmod.load()
    orig = urllib.request.urlopen

    # ① 主通道返回空内容 → 应该试备用通道并拿到它的结果
    urllib.request.urlopen = make_opener([payload_of(""), payload_of("备用通道的结果")])
    try:
        got = ops.call_llm(cfg, "sys", "user")
    finally:
        urllib.request.urlopen = orig
    check("主通道空内容 → 切到备用通道", got == "备用通道的结果", f"→ {got!r}")

    # ② 主通道抛异常 → 备用通道正常
    urllib.request.urlopen = make_opener([TimeoutError("boom"), payload_of("OK")])
    try:
        got = ops.call_llm(cfg, "sys", "user")
    finally:
        urllib.request.urlopen = orig
    check("主通道超时 → 备用通道顶上", got == "OK", f"→ {got!r}")

    # ③ 两个通道都空 → 返回 None（而不是空串，调用方能分辨）
    urllib.request.urlopen = make_opener([payload_of(""), payload_of("")])
    try:
        got = ops.call_llm(cfg, "sys", "user")
    finally:
        urllib.request.urlopen = orig
    check("两个通道都空 → 返回 None", got is None, f"→ {got!r}")

    # ④ 响应结构异常（没有 choices）→ 继续试下一个
    urllib.request.urlopen = make_opener([{"foo": 1}, payload_of("结构异常后恢复")])
    try:
        got = ops.call_llm(cfg, "sys", "user")
    finally:
        urllib.request.urlopen = orig
    check("响应结构异常 → 继续试下一个", got == "结构异常后恢复", f"→ {got!r}")

    print()
    print("PASS: 双通道容错行为正确" if not FAILS else f"FAIL: {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
