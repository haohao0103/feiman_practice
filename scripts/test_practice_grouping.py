#!/usr/bin/env python3
"""测试：练习项目归组"标题微调不档"。

场景：同一门课两次生成的课件标题微调
  轮1:「价格行为学 · 反转的原理与识别步骤」
  轮2:「价格行为学 · 反转的原理与基本步骤」
→ 必须归到**同一个项目**（第 2 轮 = round 2，而不是新建项目从 round 1 开始）

另测：不同的课不能被合并。

用法：python3 scripts/test_practice_grouping.py
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod        # noqa: E402
from webapp.core import practice_log as pl      # noqa: E402


def main():
    cfg = cfgmod.load()
    # 在**沙盒**目录里测，不碰真实练习记录
    sandbox = os.path.join(cfg["work_dir"], "_practice_grouping_test")
    shutil.rmtree(sandbox, ignore_errors=True)
    os.makedirs(sandbox, exist_ok=True)
    cfg2 = dict(cfg)
    cfg2["outputs_dir"] = sandbox
    fails = []

    def check(name, cond):
        print(f"  {'✓' if cond else '✗'} {name}")
        if not cond:
            fails.append(name)

    print("① 第 1 轮：新建项目")
    pl.archive_round(cfg2, "价格行为学 · 反转的原理与识别步骤",
                     {"score": 57.3, "trial": "第1轮"})
    check("项目已建", len(pl.list_practice_keys(cfg2)) == 1)

    print("② 第 2 轮：标题微调 → 应落到同一项目")
    pl.archive_round(cfg2, "价格行为学 · 反转的原理与基本步骤",
                     {"score": 36.4, "trial": "第2轮"})
    keys = pl.list_practice_keys(cfg2)
    check("没有新建第二个项目", len(keys) == 1)
    data = pl.load_practice(cfg2, keys[0])
    check("轮次累加到 2", len(data.get("rounds", [])) == 2)
    check("分数序列 57.3 → 36.4",
          [r["score"] for r in data["rounds"]] == [57.3, 36.4])
    check("新标题记为别名", "价格行为学 · 反转的原理与基本步骤"
          in (data.get("aliases") or []))

    print("③ 不同的课 → 不能被合并")
    pl.archive_round(cfg2, "费曼学习法 · 四维评估方法",
                     {"score": 80.0, "trial": "另一门课"})
    check("项目数 = 2", len(pl.list_practice_keys(cfg2)) == 2)

    print("④ 归组函数的边界")
    check("完全同名 → 原样返回",
          pl.resolve_practice_key(cfg2, "价格行为学 · 反转的原理与识别步骤")
          == "价格行为学 · 反转的原理与识别步骤")
    check("空 key → 原样返回", pl.resolve_practice_key(cfg2, "") == "")

    shutil.rmtree(sandbox, ignore_errors=True)
    print()
    print("PASS: 归组逻辑正确" if not fails else f"FAIL: {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
