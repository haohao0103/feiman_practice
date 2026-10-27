#!/usr/bin/env python3
"""验证「画面张数按内容自适应」：同一屏只留一张 + 每期保底 1 张，不设固定张数。

用三组真实样本（图表密集 23 张 / 场景切分 50 张 / 静态口播 3 张）跑视觉去重，
观察输出张数是否随**内容**变化而非固定值。
注意：去重会重编号并删落选文件，所以先复制到 work 下的临时目录再跑。
"""
import os
import shutil
import sys

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)
from webapp.core import courseware_gen as cg  # noqa: E402

WORK = os.path.join(ROOT, "work")
SAMPLES = [
    ("图表密集-23张 (BV1rXXfBiECa 段帧)",
     os.path.join(WORK, "bench_task/frames_clean"), 7),
    ("图表密集-50张 (场景切分)",
     os.path.join(WORK, "projects/video_to_script_20261026_163334_bc330f/frames_clean"), 7),
    ("静态口播-3张 (BV1Nw9MBYEsz)",
     os.path.join(WORK, "projects/video_to_script_20261026_202703_f9a46e/frames_clean"), 3),
]
TH = 0.03


def build_manifest(src_dir, n_parts):
    """按时间顺序把帧分配到期（模拟大纲分段），并算墨迹量。"""
    files = sorted(f for f in os.listdir(src_dir) if f.lower().endswith(".jpg"))
    n = len(files)
    manifest = []
    for i, f in enumerate(files):
        p = os.path.join(src_dir, f)
        # 时间按文件序号等分（仅用于排序与期归属）
        t = round((i + 0.5) * 600.0 / max(1, n), 1)
        manifest.append({
            "key": f"V{i+1:03d}",
            "rel_path": f"frames_clean/{f}",
            "time_sec": t,
            "note": f"样本 {i+1}",
            "part": f"第{(i % n_parts) + 1}期",
            "part_idx": (i % n_parts) + 1,
            "seg": f"段{i+1}",
            "ink": cg._ink_score(p),
        })
    return manifest


def main():
    print("=" * 78)
    print(f"画面张数自适应验证（TH={TH}，每期保底 1 张，不设固定张数）")
    print("=" * 78)
    results = []
    for name, src, n_parts in SAMPLES:
        if not os.path.isdir(src):
            print(f"\n[跳过] {name}: {src} 不存在")
            continue
        tmp = os.path.join(WORK, f"_dedupe_test_{os.path.basename(os.path.dirname(src))}")
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        # 必须复刻生产布局：frames_clean/ 在 base_dir 下
        os.makedirs(tmp, exist_ok=True)
        shutil.copytree(src, os.path.join(tmp, "frames_clean"))
        man = build_manifest(os.path.join(tmp, "frames_clean"), n_parts)
        before = len(man)
        out, stats = cg.dedupe_frames_by_visual(man, tmp, th=TH, min_per_part=1)
        # 校验：引用文件都在、编号连续
        missing = [m["rel_path"] for m in out
                   if not os.path.exists(os.path.join(tmp, m["rel_path"]))]
        keys = [m["key"] for m in out]
        seq_ok = keys == [f"V{i+1:03d}" for i in range(len(keys))]
        print(f"\n{name}")
        print(f"  抽帧原始: {before} 张  →  自适应后: {len(out)} 张")
        print(f"  合并同屏 {stats['same_screen_merged']} 张 / 期级保底补 {stats['per_part_added']} 张")
        print(f"  缺文件: {len(missing)}  编号连续: {seq_ok}")
        print(f"  保留帧时刻: {[int(m['time_sec']) for m in out]}")
        results.append((name, before, len(out), len(missing) == 0 and seq_ok))
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 78)
    print("汇总（自适应成立的关键：不同内容 → 不同张数，无固定值）")
    for name, before, after, ok in results:
        print(f"  {name:42s} {before:>3} → {after:>3} 张   校验{'通过' if ok else '失败'}")
    counts = {r[2] for r in results}
    print(f"\n  不同内容得到 {len(counts)} 种不同张数: {sorted(counts)}")
    print("  结论: " + ("张数随内容变化，自适应成立" if len(counts) > 1 else "张数固定，需再查"))
    print("=" * 78)


if __name__ == "__main__":
    main()
