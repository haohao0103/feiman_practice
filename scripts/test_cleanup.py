#!/usr/bin/env python3
"""清理逻辑测试 —— 在**临时沙盒**里跑，不碰真实的 work/。

验证 6 条安全属性：
  1) dry-run 不删任何东西
  2) 大件（frames_raw/ video/ *.wav）超龄后被删
  3) asr.txt 被保住（重跑评估的缓存不能丢）
  4) 未超龄的大件**不删**（保留期内）
  5) 受保护目录（正在跑的任务）不删
  6) 绝不碰 outputs/ 与 work/ 之外的任何路径
"""
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import cleanup as cl     # noqa: E402

SANDBOX = os.path.join(ROOT, "work", "_cleanup_test_sandbox")


def build():
    """造一个沙盒：老任务 / 新任务 / 受保护任务 / outputs 里的诱饵。"""
    if os.path.exists(SANDBOX):
        shutil.rmtree(SANDBOX, ignore_errors=True)
    work = os.path.join(SANDBOX, "work")
    out = os.path.join(SANDBOX, "outputs")
    for d in (work, out):
        os.makedirs(d, exist_ok=True)

    def task(base, name, age_days):
        t = os.path.join(base, name)
        os.makedirs(os.path.join(t, "frames_raw"), exist_ok=True)
        os.makedirs(os.path.join(t, "video"), exist_ok=True)
        for i in range(5):
            with open(os.path.join(t, "frames_raw", f"f{i}.jpg"), "w") as f:
                f.write("x" * 2000)
        with open(os.path.join(t, "video", "v.mp4"), "w") as f:
            f.write("x" * 3000)
        with open(os.path.join(t, "audio.wav"), "w") as f:
            f.write("x" * 1500)
        with open(os.path.join(t, "asr.txt"), "w", encoding="utf-8") as f:
            f.write("转写内容")
        with open(os.path.join(t, "讲课稿.md"), "w", encoding="utf-8") as f:
            f.write("# 稿")
        old = time.time() - age_days * 86400
        for root, dirs, files in os.walk(t):
            for fn in files:
                os.utime(os.path.join(root, fn), (old, old))
        for root, dirs, files in os.walk(t, topdown=False):
            os.utime(root, (old, old))
        return t

    old_t = task(os.path.join(work, "projects"), "video_to_script_20260101_100000_aaaaaa", 30)
    ancient_t = task(os.path.join(work, "projects"),
                     "video_to_script_20250101_090000_ddddddd", 600)
    ancient_t = task(os.path.join(work, "projects"),
                     "video_to_script_20250101_090000_ddddddd", 600)
    new_t = task(os.path.join(work, "projects"), "video_to_script_20260101_120000_bbbbbb", 1)
    prot_t = task(os.path.join(work, "projects"), "video_to_script_20260101_110000_cccccc", 30)
    # 诱饵：outputs/ 里的文件，清理绝不能碰
    bait = os.path.join(out, "讲课稿.pdf")
    with open(bait, "w") as f:
        f.write("PDF")
    return {"work": work, "outputs": out, "old_t": old_t, "new_t": new_t,
            "prot_t": prot_t, "ancient_t": ancient_t, "bait": bait}


def cfg_for(work, outputs):
    return {"work_dir": work, "uploads_dir": os.path.join(work, "uploads"),
            "projects_dir": os.path.join(work, "projects"),
            "outputs_dir": outputs, "logs_dir": os.path.join(work, "logs"),
            "models_dir": os.path.join(work, "models"),
            "cleanup": {"enabled": True, "keep_days": 7, "max_work_gb": 8,
                        "interval_hours": 6}}


def main():
    sb = build()
    cfg = cfg_for(sb["work"], sb["outputs"])
    fails = []

    def check(name, cond, extra=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f"  {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    print("⓪ 任务年龄排序：用目录名时间戳，不受 mtime 变动影响")
    ages = [(os.path.basename(d), round(cl._task_age_days(d), 1))
            for d in cl._task_dirs(cfg)]
    ages.sort(key=lambda x: -x[1])
    check("最老的排在最前", bool(ages) and ages[0][0].endswith("ddddddd"),
          f"→ {ages[0][0] if ages else '空'} {ages[0][1] if ages else ''} 天")

    print("⓪ 任务年龄排序：用目录名时间戳，不受 mtime 变动影响")
    ages = [(os.path.basename(d), round(cl._task_age_days(d), 1))
            for d in cl._task_dirs(cfg)]
    ages.sort(key=lambda x: -x[1])
    check("最老的排在最前", bool(ages) and ages[0][0].endswith("ddddddd"),
          f"→ {ages[0][0] if ages else '空'} {ages[0][1] if ages else ''} 天")

    print("① dry-run 不删任何东西")
    before = cl._size(sb["work"])
    st = cl.run(cfg, keep_days=7, max_gb=8, dry_run=True)
    after = cl._size(sb["work"])
    check("dry-run 后体积不变", before == after)
    check("dry-run 报告了释放量", st["freed_bytes"] > 0)

    print("② 受保护目录不被删")
    cl.protect([sb["prot_t"]])
    cl.run(cfg, keep_days=7, max_gb=8, dry_run=False)
    check("受保护任务目录还在", os.path.isdir(sb["prot_t"]))
    cl.unprotect([sb["prot_t"]])

    print("③ 超龄任务：大件被删、asr.txt 保住")
    check("老任务的 frames_raw 已删",
          not os.path.exists(os.path.join(sb["old_t"], "frames_raw")))
    check("老任务的 video 已删",
          not os.path.exists(os.path.join(sb["old_t"], "video")))
    check("老任务的 audio.wav 已删",
          not os.path.exists(os.path.join(sb["old_t"], "audio.wav")))
    check("老任务的 asr.txt 保住", os.path.exists(os.path.join(sb["old_t"], "asr.txt")))
    check("老任务的稿件保住", os.path.exists(os.path.join(sb["old_t"], "讲课稿.md")))

    print("④ 未超龄任务：不动")
    check("新任务的 frames_raw 还在",
          os.path.exists(os.path.join(sb["new_t"], "frames_raw")))
    check("新任务的 asr.txt 还在", os.path.exists(os.path.join(sb["new_t"], "asr.txt")))

    print("⑤ outputs/ 与沙盒外一律不碰")
    check("outputs 里的诱饵文件还在", os.path.exists(sb["bait"]))
    check("沙盒外没被删（仓库 README 还在）", os.path.exists(os.path.join(ROOT, "README.md")))

    print("⑥ 水位线：超限时整目录删，asr 进缓存")
    # 水位线压到 0 → 触发整目录删（用 build() 里造好的最老任务）
    # 先往里塞点东西，确保总量真的超水位线（前面的重活清理已把沙盒清空了）
    t = sb["ancient_t"]
    # 用 .bin（不在重活清理规则内）把目录撑大，确保水位线真的触发
    with open(os.path.join(t, "big.bin"), "wb") as f:
        f.write(b"x" * 400000)
    cl.run(cfg, keep_days=7, max_gb=0.0001, dry_run=False)
    cache = os.path.join(sb["work"], "asr_cache", "video_to_script_20250101_090000_ddddddd.txt")
    check("超水位线的任务目录被删", not os.path.exists(t))
    check("asr.txt 已挪进 asr_cache", os.path.exists(cache))

    print("⑧ 清理后仓库根与 outputs 未受影响")
    check("仓库 README 还在", os.path.exists(os.path.join(ROOT, "README.md")))
    check("真实 outputs/ 未被动", os.path.isdir(os.path.join(ROOT, "outputs")))

    shutil.rmtree(SANDBOX, ignore_errors=True)
    print()
    if fails:
        print(f"FAIL: {len(fails)} 项不通过 → {fails}")
        return 1
    print("PASS: 清理逻辑 7 组安全属性全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
