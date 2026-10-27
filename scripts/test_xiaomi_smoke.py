#!/usr/bin/env python3
"""冒烟测试：验证'有小米→用小米'这一半连通性。

直接拿真实 config（含有效小米双通道 key）打一次 call_llm，
确认能返回真实内容（非 None、非空）。不下载、不跑 ASR。
用法：
  python3 scripts/test_xiaomi_smoke.py 2>&1 | tee logs/test_xiaomi_smoke.log
"""
import os
import sys

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod
from webapp.core import pipeline_ops as ops

def main():
    cfg = cfgmod.load()
    chs = cfgmod.llm_channels(cfg)
    print(f"已配置通道数: {len(chs)}")
    for base, key, model, label in chs:
        print(f"  {label}: model={model} base={base} key={'*' * min(6, len(key))}...")
    assert chs, "没有任何小米通道配置 —— 不应发生（config.json 已配双通道）"

    out = ops.call_llm(
        cfg,
        "你是测试助手。只回复'OK'两个字，不要多余内容。",
        "ping",
    )
    print(f"call_llm 返回类型: {type(out).__name__} 长度: {len(out) if out else 0}")
    assert out and len(out.strip()) > 0, "小米 LLM 返回为空 —— 双通道都不可达"
    print(f"✅ 有小米→用小米 连通正常，首行预览: {out.strip()[:60]!r}")


if __name__ == "__main__":
    main()
