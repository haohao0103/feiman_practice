#!/usr/bin/env python3
"""量一下两个练习项目名的相似度，用来定"归到已有项目"的阈值。

实测场景：同一门课两次生成的课件标题微调
  「价格行为学 · 反转的原理与识别步骤」
  「价格行为学 · 反转的原理与基本步骤」
如果相似度够高，归组就不该断档（历史分数要接得上）。

用法：python3 scripts/measure_practice_key_sim.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PAIRS = [
    ("价格行为学 · 反转的原理与识别步骤", "价格行为学 · 反转的原理与基本步骤"),
    ("价格行为学 · 反转的原理与基本步骤", "价格行为学 · 价格行为学 · 反转五步骤"),
    ("价格行为学 · 反转的原理与基本步骤", "费曼学习法 · 四维评估"),
    ("价格行为学 · 反转的原理与基本步骤", "价格行为学 · 反转的原理与基本步骤"),
]


def grams(s):
    s = re.sub(r"[^\w一-鿿]", "", s or "")
    return set(s[i:i + 2] for i in range(max(1, len(s) - 1)))


def jac(a, b):
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def kw_jac(a, b):
    """关键词集合相似度（按标点/助词切词）"""
    def kws(s):
        s = re.sub(r"[·、:：|｜]", " ", s or "")
        s = re.sub(r"的|与|和|了|在|是", " ", s)
        return set(w for w in re.split(r"\s+", s) if len(w) >= 2)
    ka, kb = kws(a), kws(b)
    if not ka or not kb:
        return 0.0
    return len(ka & kb) / len(ka | kb)


if __name__ == "__main__":
    for a, b in PAIRS:
        print(f"  bigram={jac(a, b):.3f}  kw={kw_jac(a, b):.3f}   "
              f"{a[:26]} ↔ {b[:26]}")
