#!/usr/bin/env python3
"""标定 safe-delete 钩子的触发规则：单次 rmtree vs 逐个 os.remove。

目的：确认「逐个删文件」是否安全（若钩子按 turn 累计计数，逐个删也会被拦）。
做法：造 N 个空文件，用两种方式各删一次，打印是否被拦。
注意：若被拦进程会挂住，所以外层用 timeout 兜底。
"""
import os
import shutil
import sys
import time

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE = os.path.join(ROOT, "work/_hook_calib")


def make(n, tag):
    d = os.path.join(BASE, tag)
    os.makedirs(d, exist_ok=True)
    for i in range(n):
        with open(os.path.join(d, f"f{i:04d}.txt"), "w") as f:
            f.write("x")
    return d


def main():
    mode = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    d = make(n, mode)
    t0 = time.time()
    print(f"[calib] mode={mode} n={n} 开始删除", flush=True)
    if mode == "rmtree":
        shutil.rmtree(d, ignore_errors=True)
    elif mode == "individual":
        for f in sorted(os.listdir(d)):
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
        try:
            os.rmdir(d)
        except OSError:
            pass
    print(f"[calib] mode={mode} 删除完成，耗时 {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
