#!/usr/bin/env python3
"""work/ 暂存区清理（手动入口）。

用法：
  python3 scripts/cleanup_work.py --dry-run          # 只看会删什么，不动手
  python3 scripts/cleanup_work.py --apply            # 执行
  python3 scripts/cleanup_work.py --apply --keep-days 3 --max-gb 4
  python3 scripts/cleanup_work.py --status           # 只看占用

默认先跑 dry-run（按红线：删除类操作先看清楚再动手）。
日志：work/cleanup.log
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod      # noqa: E402
from webapp.core import cleanup as cl         # noqa: E402


def main():
    args = sys.argv[1:]
    c = cfgmod.load()
    keep_days = int(c.get("cleanup", {}).get("keep_days", 7))
    max_gb = float(c.get("cleanup", {}).get("max_work_gb", 8))
    for i, a in enumerate(args):
        if a == "--keep-days" and i + 1 < len(args):
            keep_days = int(args[i + 1])
        if a == "--max-gb" and i + 1 < len(args):
            max_gb = float(args[i + 1])

    if "--status" in args:
        st = cl.status(c)
        print(f"work/ 占用 {st['work_gb']}GB（水位线 {st['max_gb']}GB）"
              f"｜可清理 {st['plan_free_gb']}GB / {st['plan_items']} 项")
        return 0

    dry = "--dry-run" in args or "--apply" not in args
    p = cl.plan(c, keep_days=keep_days, max_gb=max_gb)
    print("=" * 70)
    print(f"work/ 当前占用：{p['total_bytes']/1073741824:.2f}GB"
          f"（水位线 {max_gb}GB）")
    print(f"计划清理 {len(p['items'])} 项，可释放 {p['free_bytes']/1073741824:.2f}GB")
    print(f"（大件保留 {keep_days} 天；残留目录过龄 1 天即清；"
          f"总量超水位线会从最老任务整目录删，asr.txt 先存 work/asr_cache/）")
    print("-" * 70)
    for it in sorted(p["items"], key=lambda x: -x["bytes"])[:25]:
        print(f"  {it['bytes']/1048576:9.1f}MB  [{it['kind']:11}] "
              f"{it.get('age_days', 0):5.1f}天  {it['path']}")
    if len(p["items"]) > 25:
        print(f"  … 另有 {len(p['items'])-25} 项")
    print("=" * 70)

    stat = cl.run(c, keep_days=keep_days, max_gb=max_gb, dry_run=dry)
    if dry:
        print(f"\n[试运行] 未做任何删除。真要执行：python3 scripts/cleanup_work.py --apply")
    else:
        print(f"\n[已执行] 释放 {stat['freed_bytes']/1073741824:.2f}GB，"
              f"动作 {stat['actions']} 次，work/ 现 {stat['total_bytes_after']/1073741824:.2f}GB")
    print(f"日志：{cl.LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
