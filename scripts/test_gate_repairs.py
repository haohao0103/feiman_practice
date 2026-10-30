#!/usr/bin/env python3
"""测试新增的三项闸门修补：补配图 / 补类比 / 清孤图。

在沙盒里跑（不碰真实课件目录）：
  1) repair_missing_pictures —— 无图段落补上**该段自己的抽帧**
  2) move_orphan_images —— 孤图挪到 work/ 暂存区（move 不是 delete，避开安全钩子）
  3) quality_gate 的三项判定（配图率 / 孤图率 / 类比覆盖率）确实生效

用法：python3 scripts/test_gate_repairs.py
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod            # noqa: E402
from webapp.core import courseware_gen as cg        # noqa: E402

SANDBOX = os.path.join(ROOT, "work", "_gate_repair_test")


def build():
    shutil.rmtree(SANDBOX, ignore_errors=True)
    out = os.path.join(SANDBOX, "out")
    os.makedirs(os.path.join(out, "frames_clean"), exist_ok=True)
    os.makedirs(os.path.join(out, "diagrams"), exist_ok=True)
    # 三张图：V001 被正文引用，V002/V003 待补，D001 是孤图
    for fn in ("V001.jpg", "V002.jpg", "V003.jpg"):
        with open(os.path.join(out, "frames_clean", fn), "wb") as f:
            f.write(b"x" * 100)
    with open(os.path.join(out, "diagrams", "D001.jpg"), "wb") as f:
        f.write(b"x" * 100)
    script = (
        "# 标题\n\n"
        "## 第一段：讲了什么\n\n"
        "这一段有图。\n\n"
        "![第一段](frames_clean/V001.jpg)\n\n"
        "## 第二段：另一个话题\n\n"
        "这一段没有配图，讲的是另一件事。\n\n"
        "## 第三段：第三个话题\n\n"
        "这一段也没配图。\n\n"
    )
    manifest = [
        {"key": "V001", "rel_path": "frames_clean/V001.jpg", "time_sec": 1.0,
         "note": "第一段", "part": "第1期", "seg": "第一段：讲了什么", "ink": 100},
        {"key": "V002", "rel_path": "frames_clean/V002.jpg", "time_sec": 2.0,
         "note": "第二段", "part": "第1期", "seg": "第二段：另一个话题", "ink": 90},
        {"key": "V003", "rel_path": "frames_clean/V003.jpg", "time_sec": 3.0,
         "note": "第三段", "part": "第1期", "seg": "第三段：第三个话题", "ink": 80},
    ]
    return out, script, manifest


def main():
    out, script, manifest = build()
    cfg = cfgmod.load()
    fails = []

    def check(name, cond, extra=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f"  {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    print("① 补配图：无图的段补上自己的抽帧")
    new_script, n = cg.repair_missing_pictures(script, manifest)
    check("补了 2 处", n == 2, f"→ {n}")
    check("第二段拿到了 V002", "frames_clean/V002.jpg" in new_script)
    check("第三段拿到了 V003", "frames_clean/V003.jpg" in new_script)
    check("原来那处没被动", new_script.count("frames_clean/V001.jpg") == 1)
    check("配图数 = 3", new_script.count("![") == 3, f"→ {new_script.count('![')}")

    print("② 清孤图：没被引用的图挪走（不是删）")
    moved, kept = cg.move_orphan_images(out, new_script,
                                       log=lambda m: print("   ", m.strip()))
    check("D001 被挪走", moved == 1, f"→ {moved}")
    check("frames_clean 下剩 3 张",
          len(os.listdir(os.path.join(out, "frames_clean"))) == 3)
    check("diagrams 下已空",
          len(os.listdir(os.path.join(out, "diagrams"))) == 0)

    print("③ 闸门三项判定生效")
    checks, gate = cg.quality_gate(cfg, new_script, truncated=False, out_dir=out)
    check("配图率已算出来", "seg_pic_rate" in checks, f"→ {checks.get('seg_pic_rate')}%")
    check("类比覆盖率已算出来", "analogy_seg_rate" in checks,
          f"→ {checks.get('analogy_seg_rate')}%")
    check("孤图数已算出来", "orphan_images" in checks,
          f"→ {checks.get('orphan_images')}/{checks.get('images_total')}")
    check("类比覆盖率不足被报出",
          any("类比" in g for g in gate), f"→ {gate}")

    print("④ 幂等：再跑一次不应重复补")
    again, n2 = cg.repair_missing_pictures(new_script, manifest)
    check("第二次不重复补", n2 == 0, f"→ {n2}")

    shutil.rmtree(SANDBOX, ignore_errors=True)
    print()
    print("PASS: 三项闸门修补都正确" if not fails else f"FAIL: {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
